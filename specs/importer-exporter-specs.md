# importer-exporter-specs.md
# Goal
Build the importer-exporter CLI: classify expenses from bank statements, load
investments, and export any stored entries as CSV.

# Acceptance Criteria
- importer should read the csv files from the data folder
- importer shall consult the LLM for classifying the transactions when specified so
- importer shall prepare a file called auto-rules.csv which will contain the rules which the importer prepared by consulting the LLM (auto mode) for classification
- importer shall already generate a set of major rules while we run the coding agent harness and store them as initial auto-rules.csv, the auto-rules shall be appended if there is a need
- importer in addition shall read a OPEN_ROUTER_API_KEY to consult a LLM
- importer shall read a OPENROUTER_MODEL (default openrouter/auto) to pick the LLM model
- importer shall also consult a manual-rules.csv for manually supplied rules for classification if one is present
- importer shall run in auto-mode, manual-mode and both together, or just import using existing rules
- importer shall accept the path to the manual rules file as command line argument
- importer shall accept the user for whom the data is being imported as command line argument
- importer shall acccept the type of data that is being imported bank-statement, investments
- importer shall create entries for the bank-transactions from the bank-statement after classification
- importer shall create entries for investments from the investments after classification applying the auto and  manual rules
- The importer should create entries for items where it has identified the categories with those categories
- The importer where it has not identified the categories should mark the transaction as UNKNOWN
- For default rules (no manual/auto rule matches): All money credited into the account is INCOME/INCOMING, all other debits are money spent (EXPENSE, category UNKNOWN). Investment buys are classified by the directional `@dir:wd` rules in the local rule files. The money spent, income and investments can be further sub-categorized
- rule precedence: manual-rules.csv is consulted first, then auto-rules.csv; first match wins; no match -> default (see above)
- The exporter shall provide the ability to export entries be it investments, bank-statements
- The exporter shall provide the ability to export in csv format
- The exporter shall provide various ways to filter the data to be exported
- The exporter shall be a CLI utility
- The importer shall be a CLI utility
- The importer, exporter shall have the ability to produce appropriate logs for the runs
- the database is the single source of truth: legacy repo/, review-*.csv and rules.txt are retired; data/ CSVs are the import source; output/<fy>/failed-<N>.csv is the only kept output artifact (gitignored); auto-rules.csv is seeded locally from the retired rules.txt and appended to in auto mode; all rule files are gitignored
- The importer should be log detailed failures and store the failed entries as failed.csv per each import csv in output/fy/failed.csv pattern, the file should have -<number> postfix to be unique
- failed entries (complete row) shall be written to output/<fy>/failed-<N>.csv (N = next number after existing failed-*.csv in that FY folder); the run log lists each failure with its reason; the import run continues with the remaining blocks
- failed rows are the whole reconciled block whose opening + deposits - withdrawals != closing; same exact entry (natural key) - already in the database or in a prior failed-*.csv - is skipped, never failed twice
- The importer should identify create, update and skip cases and take action accordinglu
- natural key for a bank entry = (account, date, particulars, deposit, withdrawal) per user; create (absent), update (present but stored classification differs and the row is not manual_override), skip (present and identical, or manual_override, or already recorded as failed); manual_override rows are never changed by import


