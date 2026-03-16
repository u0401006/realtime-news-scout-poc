# SkillEvo v1 — 最小執行指令
PYTHON := .venv/bin/python

.PHONY: setup backfill train test verify all

## 安裝依賴（首次）
setup:
	uv venv .venv
	uv pip install pydantic pytest

## 從 smoke_result.jsonl 回填標註資料
backfill:
	$(PYTHON) -m training_data.backfill_from_smoke \
		--input output/smoke_result.jsonl \
		--output training_data/samples/seed_20260316.jsonl

## 訓練 v1 排序模型
train:
	$(PYTHON) -m ranking.model.v1_train \
		--data training_data/samples/seed_20260316.jsonl \
		--output-weights ranking/model/v1_weights.json \
		--output-predictions ranking/model/v1_predictions.jsonl

## 執行測試
test:
	$(PYTHON) -m pytest tests/test_skillevo_v1.py -v

## 一鍵驗收：回填 → 訓練 → 測試
verify: backfill train test
	@echo "✅ SkillEvo v1 驗收通過"

## 完整流程
all: setup verify
