# Goal
To seamlessly deploy the application

# Acceptance criteria
- The application shall be available in the dist folder
- The dist folder shall be provided as a zip archive
- The dist folder shall be constructed with specific data [ user can choose to include a specific sqlitedb as initial data]
- dist/ shall contain the application sources (personal-finance.py, impex.py, backend/, finance/), requirements.txt and the README.md runbook
- a seed sqlitedb may be included via --with-db <path> (then the dist app starts on that database)
- make_dist.py shall assemble dist/ and archive it as dist.zip in the project root
- The system shall provide way to back up data
- impex.py backup shall snapshot the sqlite database (VACUUM INTO) into the gitignored backup/ folder with timestamped filenames
- impex.py restore --from <file> shall restore an earlier backup, replacing the live database
- provide a docker container way to deploy it so that no one is bothered about requirements
- A fresh clone shall start with an empty database created on first use; the README steps for the host and for Docker shall work as written; requirements.txt shall pin the versions the tests run against; a missing members.yaml shall give a clear message (with Docker, create members.yaml and auto-rules.csv before the first docker compose command, or Docker creates folders with those names).
