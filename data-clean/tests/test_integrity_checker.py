import pytest
import polars as pl
from pathlib import Path
from data_clean.src.tools.integrity_checker import IntegrityChecker

@pytest.fixture
def sample_csv(tmp_path):
    \"\"\"Creates a temporary CSV file for testing integrity checks.\"\"\"
    file = tmp_path / \"test_data.csv\"
    # Create a dataset with a clear primary key 'user_id' and some other columns
    df = pl.DataFrame({
        \"user_id\": [101, 102, 103, 104],
        \"name\": [\"Amjad\", \"Sami\", \"Noura\", \"Ali\"],
        \"email\": [\"a@test.com\", \"s@test.com\", \"n@test.com\", \"ali@test.com\"]
    })
    df.write_csv(file)
    return str(file)

def test_primary_key_detection(sample_csv):
    \"\"\"Test if the checker correctly identifies the primary key.\"\"\"
    checker = IntegrityChecker(sample_csv)
    checker.load_data()
    keys = checker.detect_primary_keys()
    assert \"user_id\" in keys
    assert len(keys) == 1

def test_critical_impact_detection(sample_csv):
    \"\"\"Test if modifying a primary key is flagged as CRITICAL.\"\"\"
    checker = IntegrityChecker(sample_csv)
    checker.load_data()
    
    # Modifying user_id should be critical
    is_critical = checker.check_critical_impact(\"user_id\", \"fill_nulls\")
    assert is_critical is True
    
    # Modifying name should NOT be critical by default
    is_critical_name = checker.check_critical_impact(\"name\", \"trim_whitespace\")
    assert is_critical_name is False

def test_relationship_stability_collision(sample_csv):
    \"\"\"Test if the checker detects integrity breaks when uniqueness is lost.\"\"\"
    checker = IntegrityChecker(sample_csv)
    checker.load_data()
    
    # Simulating a change that creates a collision in user_id (101 and 102 both become 101)
    old_vals = [101, 102, 103, 104]
    new_vals = [101, 101, 103, 104]
    
    is_stable = checker.verify_relationship_stability(\"user_id\", old_vals, new_vals)
    assert is_stable is False # Should fail because uniqueness is broken

def test_full_integrity_report(sample_csv):
    \"\"\"Test the overall integrity report generation.\"\"\"
    checker = IntegrityChecker(sample_csv)
    report = checker.get_integrity_report()
    
    assert \"detected_primary_keys\" in report
    assert \"user_id\" in report[\"detected_primary_keys\"]
    assert report[\"integrity_status\"] == \"Stable\"
