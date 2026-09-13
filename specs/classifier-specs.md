# classifier-specs.md
# Goal
Build a classifier for classifying expenses from bank statements

# Acceptance Criteria
- classifier should produce classified-<number>.csv and review-<number>.csv for each run. The number increments
- data files are never edited
- these files will be produced in output folder
- the entries in data files should be : classified-<number>.csv + review-<number>.csv
- each run will classify more from review-<number>.csv if edited to reach the goal of 0 entries in review-<number>.csv
- classifier will produce one consolidated classified-main-<number>.csv which will have all the entries of individual classified under output/<FY>/*.csv. The consolidate files will be under repo/

