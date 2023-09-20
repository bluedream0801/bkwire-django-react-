import os

### bkw_app_api_funcs ###
user_values_add_cc = [
    'first_name',
    'last_name',
    'phone_number',
    'company_name',
    'company_sector'
]

### User app settings
trial_days = 14
free_trial_level = 0
individual_level = 1
team_level = 2
mvp_level = 3
default_max_team_count = 2

subscription_price_levels = [
    {'product_name': 'trial', 'product_level': 0, 'bk_watchlist_allowed': 5, 'comp_watchlist_allowed': 10},
    {'product_name': 'individual', 'product_level': 1, 'bk_watchlist_allowed': 5, 'comp_watchlist_allowed': 10},
    {'product_name': 'team', 'product_level': 2, 'bk_watchlist_allowed': 10, 'comp_watchlist_allowed': 25},
    {'product_name': 'mvp', 'product_level': 3, 'bk_watchlist_allowed': 15, 'comp_watchlist_allowed': 100}
]

### bkw_app_email.py ###
constant_contact_from_name = 'Emily Taylor'
constant_contact_from_email_address = 'emily.taylor@bkwire.com'
