install:
	python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

venv:
	python3 -m venv .venv && . .venv/bin/activate && python -m pip install --upgrade pip && pip install -r requirements.txt

run:
	uvicorn app.main:app --reload

extract:
	python scripts/extract_price_table.py --help

test:
	pytest -q
