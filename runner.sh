#!/bin/bash
# Copy source Excel file to ratatoskr folder
/bin/cp /mnt/data/GitHub_Tools_List.xls /opt/ratatoskr/
# Change working directory
cd /opt/ratatoskr/
# Change to our home directory
cd $HOME
# Run our converter script to dump csv file instead using Pipenv
.local/bin/pipenv run python src/ratatoskr/convert_to_csv.py GitHub_Tools_List.xls GitHub_Tools_List.csv
# Export environmental variables
export GITHUB_TOKEN='REDACTED'
export GITLAB_TOKEN='REDACTED'
# Load any new entries from reference file GitHub_Tools.csv
.local/bin/pipenv run python src/ratatoskr/ratatoskr.py --load
# Run check to compare latest releases and commits from APIs to local tracker.db
.local/bin/pipenv run python src/ratatoskr/ratatoskr.py --check --provider discord
