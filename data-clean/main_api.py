from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import shutil
import os
from pathlib import Path
from typing import List, Dict, Any
import polars as pl

# Import the core agent components (relative to the data-clean project root)
from src.tools.cleaning_engine import CleaningEngine
from src.tools.quality_analyzer import QualityAnalyzer
from src.tools.integrity_checker import IntegrityChecker

app = FastAPI(title="DataClean Vertical Agent API")

# Enable CORS for the fancy frontend to communicate with the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

@app.post("/analyze")
async def analyze_data(file: UploadFile = File(...)):
    \"\"\"
    Endpoint to upload a file and get a full data quality and privacy analysis.
    \"\"\"
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="Only CSV files are supported.")

    file_path = UPLOAD_DIR / file.filename
    with open(file_path, \"wb\") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        # 1. Privacy and Quality Analysis
        analyzer = QualityAnalyzer(str(file_path))
        profile = analyzer.run_full_profile()

        # 2. Integrity Check
        checker = IntegrityChecker(str(file_path))
        integrity = checker.get_integrity_report()

        # 3. Data Preview (First 10 rows)
        df = pl.read_csv(file_path)
        preview_data = df.head(10).to_dicts()

        return {
            \"status\": \"success\",
            \"filename\": file.filename,
            \"analysis\": profile,
            \"integrity\": integrity,
            \"preview\": preview_data,
            \"recommendation\": \"Safe to proceed with basic cleaning\" if integrity[\"integrity_status\"] == \"Stable\" else \"Review critical columns before cleaning\"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/preview/{filename}")
async def get_preview(filename: str):
    \"\"\"
    Returns a sample of the data for preview purposes.
    \"\"\"
    file_path = UPLOAD_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    
    try:
        df = pl.read_csv(file_path)
        return df.head(15).to_dicts()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/clean")
async def clean_data(filename: str, actions: List[Dict[str, Any]]):
    \"\"\"
    Endpoint to perform cleaning actions based on the agent's proposal.
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

        # Get preview of cleaned data
        cleaned_df = pl.read_csv(output_path)
        cleaned_preview = cleaned_df.head(15).to_dicts()

        return {
            \"status\": \"success\",
            \"cleaned_file\": output_filename,
            \"lineage\": engine.get_lineage_df().to_dicts(),
            \"preview\": cleaned_preview,
            \"message\": \"Data cleaned successfully with full lineage tracking.\"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health_check():
    return {\"status\": \"online\", \"agent\": \"Vertical DataClean Agent v2.0\"}
