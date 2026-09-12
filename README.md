# Buy or Wait - Financial Decision Agent

An AI-powered financial decision agent built for **HackerRank Orchestrate - September 2026**.

The project focuses on a simple but challenging question:

> **"Can I afford this?"**

Instead of looking only at the user's current balance, the agent evaluates their financial situation over time and determines the safest way to handle a requested expense.

---

## About the Project

The **Buy or Wait** challenge required building a system that could make personalized financial decisions using multiple sources of information.

For every financial request, the system determines whether the user should:

- Pay in full
- Make a partial payment
- Use installments
- Wait until a later date
- Not proceed

The decision is based on the user's financial profile, financial events, future income, recurring expenses, payment preferences, available payment options, messages, and information extracted from images.

The solution generates a structured prediction for every request.

---

## Key Features

### 90-Day Financial Forecast

The agent performs a forward-looking cash-flow forecast instead of relying only on the current balance.

It considers:

- Current balance
- Minimum balance requirement
- Recurring income
- Recurring expenses
- Confirmed future payments
- Relevant financial events
- Proposed payment schedules

A payment plan is considered safe only when the user's balance remains above the required minimum throughout the forecast period.

---

### Financial Data Processing

The solution works with multiple datasets and connects them using the identifiers provided by the challenge.

The main data sources include:

- User financial profiles
- Financial events
- Financial requests
- Payment options
- Messages
- Exchange rates
- Related images

---

### Currency Conversion

Users may have different home currencies.

The agent uses the provided dated exchange rates to convert financial events into the user's home currency before performing the forecast.

---

### Message Processing

Relevant messages are processed to identify additional financial information or amendments such as:

- Changes to salary information
- Changes to financial events
- Delayed or cancelled information
- Other relevant financial updates

Messages are treated as data and cannot override the core financial safety rules.

---

### OCR for Financial Images

Some financial events contain a missing amount where the actual value is available in a related image.

The solution uses OCR to extract the amount from these images.

During development, repeated OCR processing became a performance bottleneck because the same images could be processed during multiple forecast simulations.

This was optimized by processing each image once and reusing the extracted information.

---

### Payment Plan Evaluation

The agent evaluates the available payment options and considers:

- User payment preferences
- Payment start dates
- Installment schedules
- Number of payments
- Total payable amount
- Financing fees
- Desired completion date
- Financial safety throughout the forecast

The final recommendation can be:

```text
full_payment
partial_payment
installments
wait
not_recommended
```

---

## Output

For every request, the agent generates:

| Field | Description |
|---|---|
| `request_id` | Unique request identifier |
| `amount_safe_to_pay` | Maximum amount safely payable today |
| `affordability_status` | Overall affordability decision |
| `recommended_payment_method` | Safest payment approach |
| `payment_plan` | Recommended payment schedule |
| `earliest_date_for_full_payment` | Earliest safe date for full payment |
| `spending_changes_needed` | Flexible expenses that may need adjustment |
| `decision_explanation` | Explanation supporting the decision |

The generated predictions are stored in:

```text
output.csv
```

---

## Architecture

The overall decision flow is:

```text
                    Financial Request
                           │
                           ▼
                 ┌───────────────────┐
                 │  Load User Data   │
                 └─────────┬─────────┘
                           │
                           ▼
              ┌──────────────────────────┐
              │ Reconstruct Financial    │
              │ Context                  │
              └────────────┬─────────────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
          ▼                ▼                ▼
     Financial         Messages          Images
       Events                              │
          │                                ▼
          │                               OCR
          │                                │
          └────────────────┬───────────────┘
                           ▼
                 Currency Conversion
                           │
                           ▼
                90-Day Cash-Flow
                    Forecast
                           │
                           ▼
                Safe Amount Calculation
                           │
                           ▼
               Payment Plan Evaluation
                           │
                           ▼
                 Final Recommendation
                           │
                           ▼
                       output.csv
```

---

## Project Structure

```text
buy-or-wait-financial-agent/
│
├── code/
│   ├── main.py
│   │
│   └── evaluation/
│       ├── main.py
│       └── usage_report.md
│
├── dataset/
│   ├── requests.csv
│   ├── sample_requests.csv
│   ├── financial_profiles.csv
│   ├── financial_events.csv
│   ├── exchange_rates.csv
│   ├── request_payment_options.csv
│   ├── messages.csv
│   ├── images.csv
│   └── media/
│       └── images/
│
├── output.csv
├── README.md
└── ...
```

> **Note:** The complete challenge dataset is not included in this public repository where redistribution is not permitted. The repository structure above represents the files used during development.

---

## Requirements

- Python 3.x
- `pandas`
- Tesseract OCR

Install the Python dependency with:

```bash
pip install pandas
```

Tesseract OCR is required for processing financial images.

---

## Running the Agent

From the project root:

```bash
python code/main.py
```

The program processes the provided requests and generates:

```text
output.csv
```

For the HackerRank dataset used during development, the agent generated predictions for **250 requests**.

---

## Running the Validator

After generating `output.csv`, run:

```bash
python code/evaluation/main.py
```

The evaluation script checks the submission contract, including:

- Required output columns
- Number of predictions
- Allowed decision values
- Payment constraints
- Output validity

A successful run returns:

```text
PASS
```

---

## Development & Optimization

This project was developed as part of the HackerRank Orchestrate challenge and was also my **first hands-on experience building an AI/agent-style system**.

One of the first challenges I encountered was performance.

The initial implementation repeatedly processed the same images through OCR during different financial forecast simulations. This made the complete dataset run unnecessarily slow.

I identified the repeated OCR processing as the bottleneck and changed the implementation to cache the extracted image information.

After the optimization:

```text
Requests processed: 250
Execution time:      ~24 seconds
Contract validation: PASS
```

The project was then further tested against the provided public examples to identify weaknesses in financial forecasting and decision logic.

---

## What I Learned

This project gave me practical experience with several concepts that were new to me:

- Building an agent around a real-world decision problem
- Working with multiple related datasets
- Financial state reconstruction
- Forward-looking cash-flow forecasting
- Payment-plan evaluation
- OCR and image-based data extraction
- Handling conflicting or amended financial information
- Designing validation workflows
- Debugging performance bottlenecks
- Testing decisions against known examples
- Iterating on the system based on incorrect outputs

The biggest takeaway for me was that building an agent is not just about producing an answer.

It also involves:

```text
Understand the problem
        ↓
Build the system
        ↓
Test the output
        ↓
Find incorrect decisions
        ↓
Debug
        ↓
Improve the logic
        ↓
Optimize performance
        ↓
Validate again
```

---

## HackerRank Orchestrate

**Challenge:** Buy or Wait?

**Event:** HackerRank Orchestrate - September 2026

The challenge required participants to build a runnable financial agent and submit:

- Solution code
- Prediction CSV
- AI development transcript

The system was developed and tested against the provided challenge requirements.

---

## Disclaimer

This project was created as part of a hackathon/technical challenge and is intended for educational and demonstration purposes.

It is **not a real financial advisory system** and should not be used to make actual financial decisions.

---

## Author

**Ganning Joel J**

Built as part of my learning journey into **AI agents, software engineering, and practical problem solving**.

**“This project was developed as part of the HackerRank Orchestrate challenge and was also my first hands-on experience building an AI/agent-style system."**
