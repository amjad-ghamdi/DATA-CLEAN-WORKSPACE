from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import shutil
import os
from pathlib import Path
from typing import List, Dict, Any

# Import the core agent components
from data_clean.src.tools.cleaning_engine import CleaningEngine
from data_clean.src.tools.quality_analyzer import QualityAnalyzer
from data_clean.src.tools.integrity_checker import IntegrityChecker

app = FastAPI(title="DataClean Vertical Agent API")

# Enable CORS for the fancy frontend to communicate with the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path("data-clean/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

@app.post("/analyze")
async def analyze_data(file: UploadFile = File(...)):
    \"\"\"
    Endpoint to upload a file and get a full data quality and privacy analysis.
    \"\"\"
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="Only CSV files are supported.")

    file_path = UPLOAD_DIR / file.filename
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        # 1. Privacy and Quality Analysis
        analyzer = QualityAnalyzer(str(file_path))
        profile = analyzer.run_full_profile()

        # 2. Integrity Check
        checker = IntegrityChecker(str(file_path))
        integrity = checker.get_integrity_report()

        return {
            \"status\": \"success\",
            \"filename\": file.filename,
            \"analysis\": profile,
            \"integrity\": integrity,
            \"recommendation\": \"Safe to proceed with basic cleaning\" if integrity[\"integrity_status\"] == \"Stable\" else \"Review critical columns before cleaning\"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/clean")
async def clean_data(filename: str, actions: List[Dict[str, Any]]):
    \"\"\"
    Endpoint to perform cleaning actions based on the agent's proposal.
    Actions format: [{'column': 'name', 'action': 'trim_whitespace'}, ...]
    \"\"\"
    file_path = UPLOAD_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on server.")

    try:
        engine = CleaningEngine(str(file_path))
        engine.load_data()

        for item in actions:
            col = item['column']
            action = item['action']
            
            if action == \"trim_whitespace\":
                engine.trim_whitespace([col])
            elif action == \"normalize_casing\":
                engine.normalize_casing(col, item.get('target_case', 'lower'))
            elif action == \"fill_nulls\":
                engine.fill_nulls(col, item['strategy'], item.get('fill_value'))
            elif action == \"drop_duplicates\":
                engine.drop_duplicates(subset=[col] if col != \"dataset\" else None)

        # Save cleaned version
        output_filename = f\"cleaned_{filename}\"
        output_path = UPLOAD_DIR / output_filename
        engine.save_cleaned_data(str(output_path))

        return {
            \"status\": \"success\",
            \"cleaned_file\": output_filename,
            \"lineage\": engine.get_lineage_df().to_dicts(),
            \"message\": \"Data cleaned successfully with full lineage tracking.\"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health_check():
    return {\"status\": \"online\", \"agent\": \"Vertical DataClean Agent v2.0\"}
