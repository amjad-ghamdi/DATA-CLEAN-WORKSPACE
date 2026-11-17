from typing import Any, Dict

from ..tools.quality_analyzer import QualityAnalyzer


class DataQualitySkill:
    """Turn the technical quality profile into a human-readable assessment."""

    def __init__(self, file_path: str = "assets/samples/demo.csv"):
        self.file_path = file_path

    def execute(self) -> str:
        analyzer = QualityAnalyzer(self.file_path)
        report_data = analyzer.run_full_profile()
        if "error" in report_data:
            return f"Quality assessment failed: {report_data['error']}"
        return self._format_report(report_data)

    def _format_report(self, data: Dict[str, Any]) -> str:
        report = [
            "DATA CLEAN - QUALITY PROFILE",
            f"File: {self.file_path}",
            "",
            "[Privacy]",
        ]

        privacy = data.get("privacy", {})
        report.append(f"Risk: {privacy.get('privacy_risk', 'Unknown')}")
        report.append(
            "Sensitive columns: "
            + (", ".join(privacy.get("sensitive_columns", [])) or "None detected")
        )

        report.append("\n[Missing values]")
        for column, info in data.get("missing_values", {}).items():
            if info.get("count", 0):
                report.append(
                    f"- {column}: {info['count']} missing ({info['percentage']})"
                )

        duplicates = data.get("duplicates", {})
        report.append("\n[Duplicates]")
        report.append(
            f"- {duplicates.get('duplicate_rows', 0)} duplicate rows "
            f"({duplicates.get('percentage', '0.00%')})"
        )

        report.append("\n[Transformation candidates]")
        candidates = data.get("transformation_candidates", [])
        if not candidates:
            report.append("- None detected")
        for candidate in candidates:
            report.append(
                f"- {candidate['column']}: {candidate['action']} -> "
                f"{candidate['target_type']} ({candidate['reason']})"
            )

        report.append("\n[Potential outliers]")
        outliers = data.get("outliers", {})
        any_outliers = False
        for column, info in outliers.items():
            if info.get("count", 0):
                any_outliers = True
                report.append(f"- {column}: {info['count']} potential outlier(s)")
        if not any_outliers:
            report.append("- None detected")

        return "\n".join(report)


def run_data_quality_analysis():
    return DataQualitySkill().execute()
