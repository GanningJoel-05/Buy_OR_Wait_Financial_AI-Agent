# Buy or Wait? agent

## Run

From the repository root:

```powershell
python code/main.py
python code/evaluation/main.py
```

This produces root-level `output.csv`. The solution uses only the Python standard
library; optional local Tesseract OCR is attempted only for image-only amounts.

## Approach

1. Read profiles, events, rates, payment offers, messages metadata, and image evidence.
2. Exclude cancelled, failed, unrealized investments and pending credits; reserve
   pending debits and scheduled/confirmed cash flows.
3. Infer regular category cadence from settled history and forecast 90 days.
4. Test full, partial, and eligible installment schedules against every projected
   daily balance and choose plans using the specified ordering.
5. Validate CSV structure and financial bounds using `code/evaluation/main.py`.
