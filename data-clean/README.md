# DATA CLEAN - MVP

A safer, more explainable way to clean CSV and Excel data.

## Project Goal
The goal is to provide a semi-automatic data-cleaning platform that separates safe changes from decisions that need user review, while preserving the original dataset and recording all transformations.

## Workflow
1. **Data Analysis**: Privacy check, profiling, and issue detection.
2. **Safe Cleaning**: Risk classification (SAFE, REVIEW, HIGH IMPACT) and automated safe fixes.
3. **Validation & Reporting**: Before/after validation and change log generation.

## Architecture
- **Agent**: Coordinator that decides the next step.
- **Skills**: Rule-sets for specific stages (Analysis, Cleaning, Validation).
- **Backend Tools**: Executable Python/Pandas code for data manipulation.

## Project Structure
- `src/agent/`: Coordinator logic.
- `src/skills/`: Stage-specific rules.
- `src/tools/`: Low-level data operation tools.
- `data/raw/`: Original datasets.
- `data/cleaned/`: Processed datasets.
- `tests/`: Quality and validation tests.
