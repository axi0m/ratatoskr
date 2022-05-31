#!/bin/bash
# Copy source Excel file to ratatoskr folder
/bin/cp /mnt/data/GitHub_Tools_List.xls /opt/ratatoskr/
# Change working directory
cd /opt/ratatoskr/
# Run our converter script to dump csv file instead
/home/axi0m/.local/bin/pipenv run python convert_to_csv.py GitHub_Tools_List.xls GitHub_Tools_List.csv
# Export environmental variables
export GITHUB_TOKEN='REDACTED'
export GITLAB_TOKEN='REDACTED'
# Load any new entries from reference file GitHub_Tools.csv
/home/axi0m/.local/bin/pipenv run python ratatoskr.py --load
# Run check to compare latest releases and commits from APIs to local tracker.db
/home/axi0m/.local/bin/pipenv run python ratatoskr.py --check
