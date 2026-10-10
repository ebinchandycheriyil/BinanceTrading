"""GPU deep-learning trading models on raw hourly candle sequences (PyTorch, run inside WSL).

Inputs per decision hour: the last L hourly candles (open/high/low/close vs previous close, volume vs 7-day mean,
taker-buy share, range/ATR; returns scaled by 7-day volatility) + the 48 engineered features (standardised on
training data). One network predicts both the BUY and the SELL label (multi-task).
Architectures: dilated 1D-CNN, GRU, Transformer encoder.

Protocol (frozen):
  label rule  stop 1.5 ATR, 2R target, 12h, min net 0.5% (modal rule among VALIDATION winners of ml_evolve)
  fit         2015-07 .. 2019-12, early stopping on 2020 (threshold = top-q of 2020 predictions)
  screen      random search on 2021-2022 fitness (same fitness as ml_evolve), 1 seed each
  confirm     top 4 configs x 3 seeds, averaged ensemble, re-scored on 2021-2022
  test        top 2 ensembles refit on 2015-2021 with early stopping on 2022, traded 2023 .. 2026-09 ONCE

  cd /root/pml && OMP_NUM_THREADS=4 /opt/trademl/bin/python -m daytrade.ml_deep
"""
import json
import os
import random
import time

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

from . import ml_evolve as E

OUT = os.path.join(os.path.dirname(E.OUT), 'deep')
RULE = dict(stop=1.5, rr=2.0, hold=144, min_net=0.005)
DEV = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
SEQ_COLS = 7
SPACE = dict(arch=['cnn', 'gru', 'transformer'], width=[64, 128, 256], depth=[2, 3, 4], dropout=[0.1, 0.3],
             lr=[1e-3, 3e-4], wd=[1e-4, 1e-2], L=[48, 96, 168], q=[0.90, 0.95])
N_SCREEN, N_CONFIRM, N_TEST, SEEDS = 24, 4, 2, (0, 1, 2)
T = {k: pd.Timestamp(v, tz='UTC') for k, v in dict(es1='2020-01-01', v1='2021-01-01', es2='2022-01-01',
                                                    v2='2023-01-01', end='2026-10-01').items()}


# --------------------------------------------------------------------------- data
def build():
    E._init()
    ds = E.dataset(RULE['stop'], RULE['rr'], RULE['hold'], RULE['min_net'])
    static_cols = E.feature_list(ds, 'all')
    seqs, hour_pos = {}, np.zeros(len(ds), dtype=np.int64)
    for coin, (h, f) in E._G['feat'].items():
        c = h['close']; pc = c.shift()
        vol = np.log(c).diff().rolling(168).std()
        s = pd.DataFrame({'o': np.log(h['open'] / pc), 'h': np.log(h['high'] / pc), 'l': np.log(h['low'] / pc),
                          'c': np.log(c / pc)}).div(vol, axis=0)
        s['v'] = np.log(h['volume'] / h['volume'].rolling(168).mean())
        s['tk'] = h['taker_buy_volume'] / h['volume'].replace(0, np.nan) - 0.5
        s['rg'] = (h['high'] - h['low']) / (f['atr_pct'] * c)
        s = s.replace([np.inf, -np.inf], np.nan).fillna(0.0).clip(-10, 10)
        seqs[coin] = torch.tensor(s.to_numpy(np.float32), device=DEV)
        m = (ds['coin'] == coin).to_numpy()
        hour_pos[m] = h.index.get_indexer(ds.index[m])
    return ds, static_cols, seqs, hour_pos


class Data:
    def __init__(self):
        self.ds, self.static_cols, self.seqs, self.hour_pos = build()
        self.t = self.ds.index
        self.coin = (self.ds['coin'] == 'ETH').to_numpy().astype(np.int64)
        self.y = torch.tensor(self.ds[['label_long', 'label_short']].to_numpy(np.float32), device=DEV)
        self.X = self.ds[self.static_cols].to_numpy(np.float32)

    def static(self, train_mask):
        med = np.nanmedian(self.X[train_mask], 0); med = np.where(np.isnan(med), 0, med)
        Z = np.where(np.isnan(self.X), med, self.X)
        mu, sd = Z[train_mask].mean(0), Z[train_mask].std(0) + 1e-6
        return torch.tensor(np.clip((Z - mu) / sd, -5, 5), device=DEV)

    def windows(self, rows, L):
        """(batch, SEQ_COLS, L) windows ending at each decision hour (inclusive)."""
        out = torch.zeros(len(rows), SEQ_COLS, L, device=DEV)
        for cid, coin in ((0, 'BTC'), (1, 'ETH')):
            m = self.coin[rows] == cid
            if not m.any():
                continue
            hp = torch.as_tensor(self.hour_pos[rows][m], device=DEV)
            idx = hp[:, None] - torch.arange(L - 1, -1, -1, device=DEV)[None, :]
            w = self.seqs[coin][idx.clamp(min=0)] * (idx >= 0)[..., None]
            out[torch.as_tensor(np.flatnonzero(m), device=DEV)] = w.transpose(1, 2)
        return out


# --------------------------------------------------------------------------- models
class Net(nn.Module):
    def __init__(self, cfg, n_static):
        super().__init__()
        w, d, p = cfg['width'], cfg['depth'], cfg['dropout']
        self.arch = cfg['arch']
        if self.arch == 'cnn':
            layers, ch = [], SEQ_COLS
            for i in range(d):
                layers += [nn.Conv1d(ch, w, 3, padding=2 ** i, dilation=2 ** i), nn.GELU(), nn.Dropout(p)]
                ch = w
            self.enc = nn.Sequential(*layers); enc_out = 2 * w
        elif self.arch == 'gru':
            self.enc = nn.GRU(SEQ_COLS, w, num_layers=d, batch_first=True, dropout=p); enc_out = w
        else:
            self.inp = nn.Linear(SEQ_COLS, w); self.pos = nn.Parameter(torch.zeros(1, cfg['L'], w))
            layer = nn.TransformerEncoderLayer(w, nhead=4, dim_feedforward=2 * w, dropout=p, batch_first=True)
            self.enc = nn.TransformerEncoder(layer, d); enc_out = w
        self.stat = nn.Sequential(nn.Linear(n_static, w), nn.GELU(), nn.Dropout(p))
        self.head = nn.Sequential(nn.Linear(enc_out + w, w), nn.GELU(), nn.Dropout(p), nn.Linear(w, 2))

    def forward(self, seq, stat):
        if self.arch == 'cnn':
            z = self.enc(seq); z = torch.cat([z.mean(-1), z.amax(-1)], 1)
        elif self.arch == 'gru':
            _, hn = self.enc(seq.transpose(1, 2)); z = hn[-1]
        else:
            z = self.enc(self.inp(seq.transpose(1, 2)) + self.pos).mean(1)
        return self.head(torch.cat([z, self.stat(stat)], 1))


def n_params(m):
    return sum(p.numel() for p in m.parameters())


MAX_TRAIN_SECS = 900          # wall-clock cap per training run (the best epoch so far is kept)
AMP = dict(device_type='cuda', dtype=torch.bfloat16, enabled=DEV.type == 'cuda')


def train(data, cfg, seed, fit_end, es_end, max_epochs=30, patience=4, batch=512):
    """Fit on decisions before fit_end (purged), early-stop on [fit_end, es_end).
    bf16 autocast + batch 512 keep the largest models inside the laptop GPU's 8 GB."""
    torch.manual_seed(seed); np.random.seed(seed)
    purge = pd.Timedelta(hours=RULE['hold'] / 12 + 1)
    tr = np.flatnonzero(data.t < fit_end - purge)
    va = np.flatnonzero((data.t >= fit_end) & (data.t < es_end - purge))
    stat = data.static(data.t < fit_end - purge)
    model = Net(cfg, stat.shape[1]).to(DEV)
    opt = torch.optim.AdamW(model.parameters(), lr=cfg['lr'], weight_decay=cfg['wd'])
    lossf = nn.BCEWithLogitsLoss()
    best, best_state, bad, ep = 1e9, None, 0, 0
    t_start = time.time()
    for ep in range(1, max_epochs + 1):
        model.train()
        perm = np.random.permutation(tr)
        for i in range(0, len(perm), batch):
            r = perm[i:i + batch]
            with torch.autocast(**AMP):
                logits = model(data.windows(r, cfg['L']), stat[r])
            loss = lossf(logits.float(), data.y[r])
            opt.zero_grad(); loss.backward(); nn.utils.clip_grad_norm_(model.parameters(), 1.0); opt.step()
        vl = float(lossf(predict(model, data, stat, va, cfg['L'], logits=True), data.y[va]))
        if vl < best - 1e-4:
            best, bad = vl, 0; best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
            if bad >= patience:
                break
        if time.time() - t_start > MAX_TRAIN_SECS:
            break
    model.load_state_dict(best_state)
    return model, stat, va, ep, best


@torch.no_grad()
def predict(model, data, stat, rows, L, logits=False, batch=1024):
    model.eval()
    with torch.autocast(**AMP):
        out = torch.cat([model(data.windows(rows[i:i + batch], L), stat[rows[i:i + batch]]).float()
                         for i in range(0, len(rows), batch)])
    return out if logits else torch.sigmoid(out)


# --------------------------------------------------------------------------- trading evaluation
def trade(data, probs_pred, pred_rows, thr):
    """Selected rows -> simulated trades (same simulator, costs, minimums as ml_evolve)."""
    out = []
    for j, side in enumerate(('long', 'short')):
        pos = pred_rows[probs_pred[:, j] >= thr[j]]
        s = 1 if side == 'long' else -1
        for coin, sub in data.ds.iloc[pos].groupby('coin'):
            o = E.st._orders(sub['entry_pos'].to_numpy(), s, 'market', np.nan, 1, sub[f'stop_{side}'].to_numpy(),
                             sub[f'target_{side}'].to_numpy(), RULE['hold'] - 1)
            tr = E.simulate(E._G['raw'][coin], pd.Series(dtype=float), o, E.COSTS, coin, side)
            if len(tr):
                tr['strat'] = side; out.append(tr)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def run_cfg(data, cfg, seeds, fit_end, es_end, pred_start, pred_end):
    probs_es, probs_pred, info = [], [], []
    pred_rows = np.flatnonzero((data.t >= pred_start) & (data.t < pred_end))
    for sd in seeds:
        t0 = time.time()
        model, stat, va, eps, vl = train(data, cfg, sd, fit_end, es_end)
        probs_es.append(predict(model, data, stat, va, cfg['L']).cpu().numpy())
        probs_pred.append(predict(model, data, stat, pred_rows, cfg['L']).cpu().numpy())
        info.append(dict(seed=sd, epochs=eps, val_loss=round(vl, 4), params=n_params(model), secs=round(time.time() - t0)))
    pe, pp = np.mean(probs_es, 0), np.mean(probs_pred, 0)
    thr = np.quantile(pe, cfg['q'], axis=0)
    return trade(data, pp, pred_rows, thr), info


def fitness(tr):
    y1 = E.year_stats(tr, T['v1'], T['es2']); y2 = E.year_stats(tr, T['es2'], T['v2'])
    f = min(y1['ret'], y2['ret']) - 0.5 * max(y1['maxdd'], y2['maxdd'])
    if min(y1['trades'], y2['trades']) < 40:
        f -= 1.0
    return f, y1, y2


def main():
    os.makedirs(OUT, exist_ok=True)
    rng = random.Random(2026)
    json.dump(dict(rule=RULE, space=SPACE, screen=N_SCREEN, confirm=N_CONFIRM, test=N_TEST, seeds=SEEDS,
                   splits={k: str(v) for k, v in T.items()}, device=str(DEV),
                   gpu=torch.cuda.get_device_name(0) if torch.cuda.is_available() else None, written_at=time.time()),
              open(os.path.join(OUT, 'protocol.json'), 'w'), indent=1)
    data = Data()
    print(f'data: {len(data.ds)} decisions, {len(data.static_cols)} static features, device {DEV}', flush=True)
    cfgs, seen = [], set()
    while len(cfgs) < N_SCREEN:
        c = {k: rng.choice(v) for k, v in SPACE.items()}
        if json.dumps(c, sort_keys=True) not in seen:
            seen.add(json.dumps(c, sort_keys=True)); cfgs.append(c)
    rows = []
    for i, c in enumerate(cfgs, 1):
        tr, info = run_cfg(data, c, (0,), T['es1'], T['v1'], T['v1'], T['v2'])
        f, y1, y2 = fitness(tr)
        rows.append(dict(stage='screen', cfg=json.dumps(c), fitness=f, r2021=y1['ret'], r2022=y2['ret'],
                         dd=max(y1['maxdd'], y2['maxdd']), trades=y1['trades'] + y2['trades'], **info[0]))
        print(f"screen {i}/{N_SCREEN} {c['arch']:11s} w{c['width']} d{c['depth']} L{c['L']} q{c['q']} "
              f"params {info[0]['params']:,} epochs {info[0]['epochs']} {info[0]['secs']}s -> fitness {f:+.3f} "
              f"(2021 {y1['ret'] * 100:+.1f}%, 2022 {y2['ret'] * 100:+.1f}%)", flush=True)
        pd.DataFrame(rows).to_csv(os.path.join(OUT, 'search.csv'), index=False)
    top = sorted(rows, key=lambda r: -r['fitness'])[:N_CONFIRM]
    conf = []
    for r in top:
        c = json.loads(r['cfg'])
        tr, info = run_cfg(data, c, SEEDS, T['es1'], T['v1'], T['v1'], T['v2'])
        f, y1, y2 = fitness(tr)
        conf.append(dict(cfg=c, fitness=f, r2021=y1['ret'], r2022=y2['ret'], dd=max(y1['maxdd'], y2['maxdd']), info=info))
        rows.append(dict(stage='confirm_ensemble3', cfg=json.dumps(c), fitness=f, r2021=y1['ret'], r2022=y2['ret'],
                         dd=max(y1['maxdd'], y2['maxdd']), trades=y1['trades'] + y2['trades']))
        print(f"confirm {c['arch']} w{c['width']} d{c['depth']} L{c['L']}: 3-seed ensemble fitness {f:+.3f} "
              f"(2021 {y1['ret'] * 100:+.1f}%, 2022 {y2['ret'] * 100:+.1f}%)", flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(OUT, 'search.csv'), index=False)
    finals = sorted(conf, key=lambda r: -r['fitness'])[:N_TEST]
    with open(os.path.join(os.path.dirname(OUT), 'holdout_access.log'), 'a') as fh:
        fh.write(f'{pd.Timestamp.now(tz="UTC").isoformat()} ml_deep TEST 2023-2026 {[json.dumps(f["cfg"]) for f in finals]}\n')
    years = [('2023', '2023-01-01', '2024-01-01'), ('2024', '2024-01-01', '2025-01-01'), ('2025', '2025-01-01', '2026-01-01'),
             ('2026 Jan-Sep', '2026-01-01', '2026-10-01'), ('2023-2026 all', '2023-01-01', '2026-10-01')]
    res = []
    for fr in finals:
        c = fr['cfg']
        tr, info = run_cfg(data, c, SEEDS, T['es2'], T['v2'], T['v2'], T['end'])
        stress = tr.copy()
        if len(stress):      # 1.5x fees: charge an extra half of commissions and slippage on the same trades
            stress['net'] = stress['net'] - 0.5 * (stress['fees'] + 2 * E.COSTS.slip)
        out = {y: E.year_stats(tr, pd.Timestamp(a, tz='UTC'), pd.Timestamp(b, tz='UTC')) for y, a, b in years}
        out['stress_all'] = E.year_stats(stress, T['v2'], T['end'])
        res.append(dict(cfg=c, validation=dict(r2021=fr['r2021'], r2022=fr['r2022']), test=out, info=info))
        print(f"TEST {c['arch']} w{c['width']} d{c['depth']} L{c['L']}: " +
              ' | '.join(f"{y} {out[y]['ret'] * 100:+.1f}%" for y, _, _ in years) +
              f" | DD {out['2023-2026 all']['maxdd'] * 100:.0f}% | 1.5x fees {out['stress_all']['ret'] * 100:+.1f}%", flush=True)
    json.dump(res, open(os.path.join(OUT, 'test_results.json'), 'w'), indent=1, default=float)


if __name__ == '__main__':
    main()
