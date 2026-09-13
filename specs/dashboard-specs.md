# dashboard-specs.md

# Goal
To build an application which presents investments and expenses in a nice dashboard

# Acceptance criteria
- dashboard reads from repo/classified-<main>.csv and presents various graphs, slices of data : use the best from expense and portfolio trackers. But keep simple
- dashboard should display expenses and investments based on expense and investment categories
- dashboard should provide ways to drill down information per FY
- dashboard should display various charts [most from the best expense and investment trackers]
- it should be able to track MFs : plan-name, number of units, purchase-date,purchase-nav,latest-date, latest-nav,value-at-cost,value-latest
- it should be able to track shares, ETFs similarly
- it should be able to track cash like PF,FDs,SavingsBank cash
- the purchase-nav, date, values are not mandatory, it will just show current value
- the MF/Share should show the XIRR and total return 

# Out of scope
- create historic records for MFs at monthly cadence
- MFs entries should store historic record of plan ranking per month and return of the leader on that date each month
- MF plan entries should also do an analysis of what changed for that plan each month and add a note

