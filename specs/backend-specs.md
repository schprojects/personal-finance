# Goal
To provide a backend for personal finance entries viz., expense, income data from bankstatements
and investments

# Constraints
- sqllite to be used for storage 
- use python with appropriate libraries

# Acceptance Criteria

- The system shall have the ability to store expense entries (debit)
- The system shall have the ability to store income  entries (credit)
- The system shall store the entries with a pre-determined category of expense/ income
- The system shall store investment entries with appropriate categories
- A transfer entry without a category carries the default category Transfer
- An income entry always carries the default category; edits cannot give it another category
- The system shall store the income, expense and investment categories
- The system shall store the income, expense and investment entries with the owner of the entry [ the person whom the entry belongs]
- The system shall store the users 
- The system shall provide ability to group users into a family unit
- The system shall provide ability to search entries (income, expense, investment) based on various criteria
- The system shall provide ability to update entries
- The system shall provide ability to store entries per financial year 
- The system shall provide ability to define the period of financial year
- the financial year default is April start (Apr-Mar), configurable in settings
- the sqlite database file lives at var/networth.db by default (gitignored), path overridable via FINANCE_DB env or CLI flag
- The database path is the FINANCE_DB environment variable, else FINANCE_DB in a gitignored .env file next to the code (a relative path is relative to the project folder), else var/networth.db.
- The system shall provide ability to upgrade schema from one version to other
- The systen shall provide following investment types : MF,share,cash,EPF,FD,Loan-Receivable, ETFs etc (choose a minimum based on best tools simplified for the current requirements)
- The system shall provide a means to track the current value of the investments
- The system shall provide a means to track  the profit / loss made on the investments, with XIRR
- The system shall provide only the current value if there is historical data to determine XIRR is not available
- The system shall track MFs : plan-name, number of units, purchase-date,purchase-nav,latest-date, latest-nav,value-at-cost,value-latest
- The purchase-nav, date, values are not mandatory, it will just show current value
- The system shall track shares and ETFs
- The MF/Share should show the XIRR and total return if data is available for it
- The system shall use Mint/Simplifi as inspiration for the schema and functionality but very simple and limited as it is for specific requirements
- System shall provide following initial categories
      "Bank_DP_charges
       Bills_aggregators
       Cash_withdrawals
       Dining_out
       Donations_gifts
       Family_support
       Fitness_Sports
       Friends
       Groceries
       Healthcare_pharmacy
       Income_tax
       Insurance
       Internet_phone
       Kids_school
       Learning_upskilling
       Legal_professional
       Loan_EMI
       Misc_small
       Shopping_goods
       Subscriptions_digital
       Transport_fuel
       Travel_leisure
       Utilities
       Vehicles
       UNKNOWN"
- System shall provide following initial kinds:
    "expense
     refund
     income 
     investment
     transfer"

# Out of scope
- create historic records for MFs at monthly cadence
- MFs entries should store historic record of plan ranking per month and return of the leader on that date each month
- MF plan entries should also do an analysis of what changed for that plan each month and add a note
