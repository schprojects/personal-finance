# Demo database

`demo.db` contains synthetic data for `user-A` and `user-B` across five Indian
financial years (FY 2022-23 through FY 2026-27). The current year ends in
October 2026. Both demo users use the password `demo`.

Run the dashboard against it with:

```sh
FINANCE_DB=demo/demo.db streamlit run personal-finance.py
```

Or create the deployable `dist/` bundle with this database preloaded:

```sh
python3 scripts/make_dist.py --with-db demo/demo.db
```

This committed database is the deployment seed. It and its credentials are
for demonstrations only; review any replacement carefully to ensure that it
contains synthetic data and no personal information.
