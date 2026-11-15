"""Read-only CLI for quality assessment and cleaning recommendations."""
import argparse

from src.skills.data_quality_skill import DataQualitySkill
from src.recommendations import run_cleaning_plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset', nargs='?', default='data/raw/sample_data.csv')
    parser.add_argument('--task', choices=('quality', 'plan', 'both'), default='both')
    args = parser.parse_args()
    if args.task in ('quality', 'both'):
        print(DataQualitySkill(args.dataset).execute())
    if args.task in ('plan', 'both'):
        print(run_cleaning_plan(args.dataset))


if __name__ == '__main__':
    main()
