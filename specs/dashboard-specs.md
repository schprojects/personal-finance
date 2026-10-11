# dashboard-specs.md

# Goal
To build an application which presents investments and bank-transactions in a nice dashboard

# Constraints
- use streamlit and plotly for drawing charts


# Acceptance criteria

- dashboard shall display bank-transactions and investments based on various categories
- dashboard shall provide ability to show bank transactions per user with ability to filter on types and other various fields from the schema
- dashboard shall provide the ability to investments per user with ability to filter on types and other various fields from the schema
- dashboard shall provide the ability to show various charts for the data being shown as the case may be : pie, bar, column,line graph etc  [most from the best bank-transactions and investment trackers]
- dashboard shall provide the ability to show data for a family unit 
- dashboard should provide ways to drill down information per FY,various fields of bank-statements, fields of investments
- it should have the ability to show details per person and also for the family with an option to choose
- in the family view nothing should be editable
- every view must aggregate the items for that user and for that scope
- there should be a view to create new categories and kinds
- each page should show the signed in user and the scope "family" or the "member name" as radio button
- landing/home screen: refer to the ui-specs landing page: it should show 
  - overview
  - categories
  - transactions
  - holdings
  - import
  - export links, each click leading to a specific screen

- overview screen : follow as per the ui-specs scheme
  - income,refund,transfer are incoming and should be net-income if credited to the account
  - investment though debit should be counted separately and they are not expenses
  - expense labeled are expenses.
  - Top category and the Top categories chart total each category across all selected financial years (a category that spans several years is one entry).
  - A debited transfer is spend: its withdrawal is added to net spend under the default category Transfer. A credited transfer is incoming and is never netted against spend. Investments are never spend.
  - Incoming is the sum of credited income, credit, refund and transfer rows. Net spend is the sum of (withdrawal − deposit) over expense rows only; refunds are not netted against spend. A credit on an expense row reduces that expense and is not incoming.
    use these facts in your calculations
  - the sidebar has a "Daily-spend baseline" option. When selected, spend in these categories is left out of every overview figure and chart: Income_tax, Vehicles, Loan_EMI and Bank_DP_charges. These are large or irregular amounts that hide day-to-day spending. Income, investments and transfers are not affected.
  - the "How is Net spend calculated?" section shall state the formula Net spend = money paid out on expenses, minus any money credited back on those same expenses, plus money paid out in transfers. Refunds and money received in transfers count as incoming, not as a reduction of spend. The section shall list every filter and option that removes rows", each with the ₹ amount it removes by itself: financial years, categories, maximum single amount, big-ticket exclusions and the daily-spend baseline. For the baseline it shall show whether it is on or off and, for each of the four categories, the category name and its net spend for the selected scope and years, labelled "excluded" when the option is on and "would be excluded" when it is off, so the user can see exactly what the option hides.
  - show a list of big ticket spends for the selected year and provide an option to exclude them from calculations
  - the sidebar has a "Daily-spend baseline" option. When selected, spend in these categories is left out of every overview figure and chart: Income_tax, Vehicles, Loan_EMI and Bank_DP_charges. These are large or irregular amounts that hide day-to-day spending. Incom investments and transfers are not affected.
  - the "How is Net spend calculated?" section shall state the formula (net spend = Σ (withdrawal − deposit) over expense and refund rows that pass the filters) and shall list every filter and option that removes rows, each with the ₹ amount it removes by itself: financial years, categories, maximum single amount, big-ticket exclusions and the daily-spend baseline. For the baseline it shall show whether it is on or off and, for each of the four categories, the category name and its net spend for the selected scope and years, labelled "excluded" when the option is on and "would be excluded" when it is off, so the user can see exactly what the option hides.  

- transactions screen : follow as per the ui-specs scheme
  - should be possible to do kind and category update for one or multiple rows
  - should NOT save automatically, save is explicit and batched (so that save is not triggered on each value selection)
  - should be possible to filter based on string or sql query
  - string filter and sql filter are exclusive, selecting both should throw an error.
  - should be possible to bulk apply changes to kind and category fields
  - system should maintain the same rows when the kind and category are updated.(the data should not disappear)
  - system shall provide the ability to search transaction like some SQL statement select * where kind is empty and category is empty and particulars contains 'something' and multiple variations and it should be possible to apply any kind and category etc to those filtered values
  - System shall provide a selection box to select a particular row or all rows when a filter is applied
  - System shall retain kind and category edits made in the table immediately in the working session, per cell, as the user makes them (in memory only, not on disk); editing kind and/or category across multiple rows in one pass must retain all the changes when the user explicitly saves or moves on (not only the last edited row); the changes reach disk only through the explicit save/export actions above

- imports screen: follow as per ui-specs scheme
  - system shall import the csv file and apply the kind, category as per the rules in auto-rules.csv, read from the project folder (the standard path); the rules are never stored in the database
  - the rules file has one rule per line as KIND,CATEGORY,EXPRESSION; '|' separates alternative texts; @dir:dep or @dir:wd limits a rule to credits or debits; @amt<=N, @amt>N (also <, >=, =) limit it by amount; the first matching rule wins; lines that cannot be read are skipped and listed on the screen
  - system shall mark unmatched entries as UNKNOWN
  - successful import should take to the transactions screen
  - system should have this precedence -> values loaded from csv, if empty kind /category and matching autorule then use, if user overrides then it takes precedence
  - System shall show an "Apply auto-rules" option when selected will render the data with auto-rules, when de-selected it will just show all the  data. If auto-rules.csv is missing the screen says where to put it
  - system shall apply the auto rules and populate in memory and when save is applied then committed to database
  - system should provide a way to lookup fullpath of file to import
  - system shall provide the year and member for whom data is being imported

- categories screen : follow as per ui-specs scheme
 - should provide a way to create new category
 - should be possible to create a new kind 

- holdings view: follow as per ui-specs scheme
 - should provide a way to look holding per filters : types of holdings, family or member
 - should be able to update purchase date, nav etc not the primary key of the entry

- exports view : follow as per ui-specs schems
  - should provide ability to export per year, member filters
  - should either specify the path or default path will be taken for export, if file already present then a suffix (number) is used

# Out of scope
- create historic records for MFs at monthly cadence
- MFs entries should store historic record of plan ranking per month and return of the leader on that date each month
- MF plan entries should also do an analysis of what changed for that plan each month and add a note

