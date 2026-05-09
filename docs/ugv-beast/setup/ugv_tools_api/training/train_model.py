#!/usr/bin/env python3
"""Train a compact DNN head on openwakeword (16,96) embedding features.

Model architecture mirrors openwakeword.train.py Net (flatten→FC→LN→ReLU + blocks→sigmoid).
Input: (B, 16, 96) float32
Output: (B, 1) sigmoid probability
Exports to ONNX at spec path.
"""
import argparse
import os
import sys
from pathlib import Path

import numpy as np
import torch
from torch import nn, optim
from torch.utils.data import TensorDataset, DataLoader


class FCNBlock(nn.Module):
    def __init__(self, layer_dim):
        super().__init__()
        self.fcn_layer = nn.Linear(layer_dim, layer_dim)
        self.relu = nn.ReLU()
        self.layer_norm = nn.LayerNorm(layer_dim)

    def forward(self, x):
        return self.relu(self.layer_norm(self.fcn_layer(x)))


class WakeWordDNN(nn.Module):
    """Matches openwakeword.train.Net (DNN variant) so it loads via openwakeword.Model."""

    def __init__(self, input_shape=(16, 96), layer_dim=128, n_blocks=1, n_classes=1):
        super().__init__()
        self.flatten = nn.Flatten()
        self.layer1 = nn.Linear(input_shape[0] * input_shape[1], layer_dim)
        self.relu1 = nn.ReLU()
        self.layernorm1 = nn.LayerNorm(layer_dim)
        self.blocks = nn.ModuleList([FCNBlock(layer_dim) for _ in range(n_blocks)])
        self.last_layer = nn.Linear(layer_dim, n_classes)
        self.last_act = nn.Sigmoid() if n_classes == 1 else nn.ReLU()

    def forward(self, x):
        x = self.relu1(self.layernorm1(self.layer1(self.flatten(x))))
        for block in self.blocks:
            x = block(x)
        x = self.last_act(self.last_layer(x))
        return x


def train_epoch(model, loader, opt, loss_fn, device, pos_weight=1.0):
    model.train()
    total_loss = 0.0
    correct = 0
    total = 0
    tp = fp = tn = fn = 0
    for x, y in loader:
        x, y = x.to(device), y.to(device).float().unsqueeze(1)
        opt.zero_grad()
        p = model(x)
        # Weighted BCE: up-weight positive class
        weights = torch.where(y > 0.5, pos_weight, 1.0).to(device)
        loss = (loss_fn(p, y, reduction="none") * weights).mean()
        loss.backward()
        opt.step()
        total_loss += loss.item() * x.size(0)
        preds = (p > 0.5).float()
        correct += (preds == y).sum().item()
        total += x.size(0)
        tp += ((preds == 1) & (y == 1)).sum().item()
        fp += ((preds == 1) & (y == 0)).sum().item()
        tn += ((preds == 0) & (y == 0)).sum().item()
        fn += ((preds == 0) & (y == 1)).sum().item()
    return total_loss / total, correct / total, tp, fp, tn, fn


@torch.no_grad()
def eval_epoch(model, loader, device):
    model.eval()
    tp = fp = tn = fn = 0
    all_scores_pos = []
    all_scores_neg = []
    for x, y in loader:
        x, y = x.to(device), y.to(device).float().unsqueeze(1)
        p = model(x)
        preds = (p > 0.5).float()
        tp += ((preds == 1) & (y == 1)).sum().item()
        fp += ((preds == 1) & (y == 0)).sum().item()
        tn += ((preds == 0) & (y == 0)).sum().item()
        fn += ((preds == 0) & (y == 1)).sum().item()
        all_scores_pos.extend(p[y == 1].cpu().numpy().flatten().tolist())
        all_scores_neg.extend(p[y == 0].cpu().numpy().flatten().tolist())
    prec = tp / max(tp + fp, 1)
    rec = tp / max(tp + fn, 1)
    return {
        "precision": prec, "recall": rec, "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "mean_pos_score": float(np.mean(all_scores_pos)) if all_scores_pos else 0.0,
        "mean_neg_score": float(np.mean(all_scores_neg)) if all_scores_neg else 0.0,
        "max_neg_score": float(np.max(all_scores_neg)) if all_scores_neg else 0.0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--features", required=True, help=".npz from extract_features.py")
    ap.add_argument("--out-onnx", required=True)
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--batch-size", type=int, default=256)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--layer-dim", type=int, default=128)
    ap.add_argument("--n-blocks", type=int, default=1)
    ap.add_argument("--val-frac", type=float, default=0.15)
    ap.add_argument("--pos-weight", type=float, default=5.0,
                    help="BCE weight on positive class (imbalanced data)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    data = np.load(args.features)
    X = data["X"].astype(np.float32)
    y = data["y"].astype(np.int64)
    n = len(X)
    print(f"[*] loaded features: {X.shape}, pos rate: {y.mean():.3f}")

    # Shuffle + split
    idx = np.random.permutation(n)
    X, y = X[idx], y[idx]
    n_val = int(n * args.val_frac)
    X_train, X_val = X[n_val:], X[:n_val]
    y_train, y_val = y[n_val:], y[:n_val]
    print(f"[*] train: {len(X_train)} (pos={y_train.sum()}), val: {len(X_val)} (pos={y_val.sum()})")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[*] device: {device}")

    train_ds = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train))
    val_ds = TensorDataset(torch.from_numpy(X_val), torch.from_numpy(y_val))
    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=args.batch_size, shuffle=False, num_workers=0)

    model = WakeWordDNN(input_shape=(16, 96), layer_dim=args.layer_dim, n_blocks=args.n_blocks).to(device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"[*] model params: {n_params:,}")

    opt = optim.Adam(model.parameters(), lr=args.lr)
    sched = optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)
    loss_fn = torch.nn.functional.binary_cross_entropy

    best_val_score = -1
    best_state = None
    for epoch in range(args.epochs):
        tl, tacc, tp, fp, tn, fn = train_epoch(model, train_loader, opt, loss_fn, device,
                                               pos_weight=args.pos_weight)
        sched.step()
        val = eval_epoch(model, val_loader, device)
        # Objective: high recall with low max_neg_score (false-positive peak)
        score = val["recall"] - 2 * val["max_neg_score"]
        print(f"[ep {epoch+1:02d}] loss={tl:.4f} tacc={tacc:.3f} "
              f"val_p={val['precision']:.3f} val_r={val['recall']:.3f} "
              f"pos={val['mean_pos_score']:.3f} neg_mean={val['mean_neg_score']:.3f} "
              f"neg_max={val['max_neg_score']:.3f} score={score:.3f}")
        if score > best_val_score:
            best_val_score = score
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            print(f"    [best] saved state, score={score:.3f}")

    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()

    # Final validation
    val = eval_epoch(model, val_loader, device)
    print(f"[final] precision={val['precision']:.3f} recall={val['recall']:.3f} "
          f"mean_pos={val['mean_pos_score']:.3f} max_neg={val['max_neg_score']:.3f}")

    # Export to ONNX — force the legacy tracing exporter (the new dynamo exporter
    # in PyTorch 2.11+ requires onnxscript and is less stable for tiny models).
    os.makedirs(os.path.dirname(args.out_onnx), exist_ok=True)
    dummy = torch.rand(1, 16, 96)
    torch.onnx.export(
        model.to("cpu"), dummy, args.out_onnx,
        input_names=["input_1"], output_names=["black_box_flight_recorder"],
        opset_version=14,
        dynamic_axes={"input_1": {0: "batch"}, "black_box_flight_recorder": {0: "batch"}},
        dynamo=False,
    )
    size_kb = os.path.getsize(args.out_onnx) / 1024
    print(f"[done] onnx saved: {args.out_onnx} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
