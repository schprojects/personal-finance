# SPEC.md

# Goal
Family networth, expenses  expense tracker. `README.md` is the runbook.

# Costraints
- ~~use csv files as truth for now~~
- use database as backend
- use python for import-export and application both
- ~~application to be read-only for now - just serving various charts and figures~~
- application should both serve the charts and entries - edit and read both uses
- use Simplify/Mint as inspiration but much simpler for a specific family requirements

# Acceptance Criteria
- The family members(both spouses) need to track their investments and expenses
- They need to be able to make decisions based on the current state of the investments
- The members are stored in a file called members.yaml which provides name and the root directory data pertaining to them
- data contains all the data for the members in this structure
    - data /
       - <member-name>
         - bank-statements 
            - <FY> (example : [FY])
                    - <name>.csv
         - investments
            - <name.csv> (may be mutiple)
- There are few areas this application deal with :
    - importing the data in csv into datastore
    - exporting the data in csv format
    - importer should classify raw bank-statements to expense, income with many subcategories and create entries in datbase
    - importer should import likewise investments
    - exporter should be able to export data in csv format
    - importer-exporter is a single CLI tool
    - Backend for the application which will be the system of record
    - Dashboard application serving the data(investments and bank-transactions): it will serve all transactions(investments and bank statement entries) and one can filter, draw charts based on the transactions : expense, income, various categories, FY being analyzed etc (LLM can check best practices)
    - investments can be MutualFunds, Shares and Cash components, wherever possible one may specify some mechanism to track the latest values of those investments
    - see importer-expoter-specs.md for importer-exporter specifications
    - see backend-specs.md for the backend specification
    - see dashboard-specs.md for dashboard specifications
    - see deploy-bakup-specs.md for deployment and backup specifications
    - see auth-specs.md for authentication specifications

# Out of scope 
- ~~database backed application is currently not scope~~

