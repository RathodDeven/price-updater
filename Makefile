install:
	python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

run:
	uvicorn app.main:app --reload

test:
	pytest -q
