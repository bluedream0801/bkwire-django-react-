#!/usr/bin/python
# -*- coding: latin-1 -*-

import stripe
import os, sys
from os import path
import logging.config
from logtail import LogtailHandler

sys.path.insert(0, '/app')
from modules.database.db_select import dbSelectItems
from modules.stripe import bkw_stripe_config
from sub_apps.bkw_app_email import BkAppEmailFunctions
### LOGGING SETUP ###
handler = LogtailHandler(source_token=os.environ['LOG_SOURCE_TOKEN'])

logger = logging.getLogger(__name__)
logger.handlers = []
logger.setLevel(logging.DEBUG) # Set minimal log level
logger.addHandler(handler) # asign handler to logger

class StripeIntegrate:
    def __init__(self):
        self.stripe_api_key = bkw_stripe_config.stripe_api_key
        self.stripe_webhook_secret = bkw_stripe_config.stripe_webhook_secret
        self.return_url = bkw_stripe_config.return_url
        
        stripe.api_key = self.stripe_api_key
        self.stripe_api = stripe

        self.db1 = dbSelectItems()
        self.db1.db_login()

    def webhook(self, payload, sig_header):
        event = None
        eventType = ''
        cust_email_string = None
        # Parse product_name from meta data
        # upodate the user entry sub_level accordingly
        logger.debug('Stripe Webhook Triggered')
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, self.stripe_webhook_secret
            )
        except ValueError as e:
            # Invalid payload
            logger.error(f"Stripe Error: {e}")
        except stripe.error.SignatureVerificationError as e:
            # Invalid signature
            logger.error(f"Stripe Error: {e}")
        eventType = event['type']
        
        # New customers, update ID in database
        if event.type == 'customer.created':
            cust_id = event.data['object']['id']
            customer_email = event.data['object']['email']
            sql_query = f"UPDATE users SET customer_id = '{cust_id}' WHERE email_address = '{customer_email}'"

            logger.debug(f"Stripe Webhook create new user: {sql_query}")
            self.db1.sql_commit(sql_query)

        elif event.type == 'customer.subscription.created' or event.type == 'customer.subscription.updated':
            logger.debug(f"Stripe Subscription Created/Updated: {event.data}")
            subscription_price_level = 0
            if 'product_name' in event.data['object']['items']['data'][0]['plan']['metadata']:
                product_name = event.data['object']['items']['data'][0]['plan']['metadata']['product_name']
                logger.debug(f"Stripe Event Data: product_name: {product_name}")
                for p in bkw_stripe_config.subscription_price_levels:
                    if p['product_name'] == product_name:
                        subscription_price_level = p['product_level']
            else:
                logger.error(f"Stripe Webhook: product_name Not Found in Metadata")  
                          
            # Set new values for User Info update in database
            sub_id = event.data['object']['id']
            sub_status = event.data['object']['status']
            sub_price_id = event.data['object']['items']['data'][0]['price']['id']
            sub_price_level = subscription_price_level
            cust_id = event.data['object']['customer']
            
            # Check the current value before update - take necessary action on downgrades
            sql_query_user_info = f"SELECT * FROM users WHERE customer_id = '{cust_id}'"
            user_info_results = self.db1.fetch_no_cache(sql=sql_query_user_info)

            try:
                # If we downgraded remove watchlist
                if sub_price_level < user_info_results[0]['subscription_price_level']:
                    user_id = user_info_results[0]['id']
                    # clear watchlist - as a user could add max watchlist and downgrade and still get notified
                    sql_bk_del = f"DELETE FROM `bankruptcies_watchlist` WHERE user_id = {user_id};"
                    self.db1.sql_commit(sql_bk_del)

                    sql_comp_del = f"DELETE FROM `companies_watchlist` WHERE user_id = {user_id};"
                    self.db1.sql_commit(sql_comp_del)
            except Exception as e:
                logger.error(f"bkw_stripe downgrade check: {e}: {user_info_results}")

            try:
                # If user is downgrading from TEAM take action
                if user_info_results[0]['subscription_price_level'] == 2 and sub_price_level != 2:
                    # If the user was on Team Membership but no longer - need to remove team members access
                    user_sql_query = f"SELECT * FROM user_team WHERE principal_email_address = '{user_info_results[0]['email_address']}'"
                    user_team = self.db1.fetch_no_cache(sql=user_sql_query)
                    sql_team_del = f"DELETE FROM user_team WHERE principal_email_address = '{user_info_results[0]['email_address']}'"
                    self.db1.sql_commit(sql_team_del)

                    # Loop through team members and set them to 0 (free trial)
                    for u in user_team:
                        team_member_email = u['member_email_address']
                        # update db with new sub info for user
                        sql_update = f"UPDATE users SET \
                        subscription_id = '', \
                        subscription_status = '', \
                        subscription_price_id = '', \
                        subscription_price_level = 0 \
                        WHERE email_address = '{team_member_email}'"

                        logger.debug(f"Stripe Webhook SQL_QUERY update user: {sql_update}")
                        self.db1.sql_commit(sql_update)
            except Exception as e:
                logger.error(f"bkw_stripe Downgrade Team actions: {e} {user_info_results}") 

            # update db with new sub info for user
            sql_update = f"UPDATE users SET \
            subscription_id = '{sub_id}', \
            subscription_status = '{sub_status}', \
            subscription_price_id = '{sub_price_id}', \
            subscription_price_level = {sub_price_level} \
            WHERE customer_id = '{cust_id}'"

            logger.debug(f"Stripe Webhook SQL_QUERY update user: {sql_update}")
            self.db1.sql_commit(sql_update)
            # If a user signs up for Team Membership(only leaders can sign up )
            # query DB using cust_id for email to trigger
            try:
                cust_email = self.db1.fetch_no_cache(sql=f"SELECT email_address FROM users WHERE customer_id = '{cust_id}'")
                logger.debug(f"cust_email: {cust_email}")
                cust_email_string = cust_email[0]['email_address']
                for p in bkw_stripe_config.subscription_price_levels:
                    if p['product_name'] == 'team':
                        if p['product_level'] == sub_price_level:
                            logger.debug(f"Team Subscription Detected: {cust_email_string}")
                            # Update the user max_count in DB to default 2

                            sql_query = f"UPDATE users SET \
                            max_team_count = 2 \
                            WHERE customer_id = '{cust_id}'"

                            self.db1.sql_commit(sql_query)

                            # Trigger Team Leader Email
                            BKAEF = BkAppEmailFunctions()
                            BKAEF.build_campaign_email(sub_line='BKwire Team Membership',
                                                    camp_name=f'BKwire Team Membership - {cust_email_string}',
                                                    email_address=[cust_email_string],
                                                    list_name='BKwire Team Leader List - recycle',
                                                    html_temp_name='bkw_team_leader')
            except Exception as e:
                logger.error(f"Stripe Webhook: Failed to send Team Leader Email: {e} - {cust_id}")
                        
        elif event.type == 'customer.subscription.deleted':
            # DB update on sub delete
            sub_id = "null"
            sub_status = "inactive"
            sub_price_id = "null"
            sub_price_level = 0 
            cust_id = event.data['object']['customer']

            sql_query = f"UPDATE users SET \
            subscription_id = '{sub_id}', \
            subscription_status = '{sub_status}', \
            subscription_price_id = '{sub_price_id}', \
            subscription_price_level = {sub_price_level} \
            WHERE customer_id = '{cust_id}'"

            self.db1.sql_commit(sql_query)

        elif event.type == 'invoice.paid':
            # DB update on invoice paid
            sql_query = f"UPDATE users SET subscription_status = 'active' WHERE customer_id = '{event.data['object']['customer']}'"
            self.db1.sql_commit(sql_query)
        elif event.type == 'invoice.payment_failed':
            # DB update on payment failed
            sql_query = f"UPDATE users SET subscription_status = 'past_due' WHERE customer_id = '{event.data['object']['customer']}'"
            self.db1.sql_commit(sql_query)
        else:
            logger.warning('Unhandled event type {}'.format(event.type))

        return ({'success': True})

    def create_customer(self, customer_email):
        stripe_create_result = stripe.Customer.create(
            description="BKwire Customer",
            email=customer_email
        )
        return stripe_create_result

    def customer_portal(self, **kwargs):
        #use RETURN_URL from ENV Vars
        portalSession = self.stripe_api.billing_portal.Session.create(
            customer=kwargs['customer_id'][0],
            return_url=self.return_url
        )
        return (portalSession['url'])

    def update_subscription(self, user_data, **kwargs):
        is_trial = kwargs['trial'][0] == 'true';
        trial_days = bkw_stripe_config.trial_days if is_trial == True else None

        if user_data['subscription_id'] and is_trial == True:
            raise Exception("User cannot start another trial")

        if user_data['subscription_id']:
            logger.debug(f"Updating user subscription")
            sub_ret_obj = self.stripe_api.Subscription.retrieve(user_data['subscription_id'])
            if kwargs['price_id'][0] == "":
                items_id_price = {
                    "id": sub_ret_obj['items']['data'][0]['id']
                    }
            else:
                items_id_price = {
                    "id": sub_ret_obj['items']['data'][0]['id'],
                    "price": kwargs['price_id'][0]
                    }
            resp = self.stripe_api.Subscription.modify(
              user_data['subscription_id'],
              cancel_at_period_end = False,
              payment_behavior = "allow_incomplete",
              proration_behavior = "always_invoice",
              items = [items_id_price]
            )
            logger.debug(f"stripe update_subscription response: {resp}")
            return self.return_url
        else:
            #if the user does NOT have a subscription, create a checkout session and redirect to it
            logger.debug(f"Creating checkout session")
            session = self.stripe_api.checkout.Session.create(
                mode="subscription",
                customer=kwargs['customer_id'][0],
                success_url=self.return_url,
                cancel_url=self.return_url,
                line_items=[
                    {
                      "price": kwargs['price_id'][0],
                      "quantity": 1
                    }
                ],
                subscription_data={"trial_period_days": trial_days}
            )
            return session['url']

    def get_price_ids(self, product):        
        price_ids = self.stripe_api.Price.search(
                query=f"active: 'true' AND product:'{product}'",
            )
        
        return price_ids

    def get_product_ids(self):        
        price_search_res = []
        for p in bkw_stripe_config.subscription_price_levels:
            price_search_res.append(self.stripe_api.Product.search(
                query=f"active:'true' AND metadata['product_name']:'{p['product_name']}'",
            ))
        return price_search_res

    def get_user_subs(self, customer_id):
        sub_list_results = self.stripe_api.Subscription.list(customer=customer_id)
        return sub_list_results

    def test_events(self, event_data, subscription_price_level):
        product_name = event_data['data']['object']['items']['data'][0]['plan']['metadata']['product_name']
        for p in bkw_stripe_config.subscription_price_levels:
            if p['product_name'] == product_name:
                subscription_price_level = p['product_level']
            if p['product_name'] == 'team':
                if p['product_level'] == subscription_price_level:
                    print(f"Team Leader Email Triggered")
                    # Trigger Team Leader Email       
#        cust_email = event_data['data']['object']['customer_email']
#
#        # If a user signs up for Team Membership(only leaders can sign up )
#        for p in bkw_stripe_config.subscription_price_levels:
#            if p['product_name'] == 'team':
#                if p['product_level'] == subscription_price_level:
#                    logger.info(f"Team Leader Email Triggered: {cust_email}")
#                    # Trigger Team Leader Email
#                    BKAEF = BkAppEmailFunctions()
#                    BKAEF.build_campaign_email(sub_line='BKwire Team Membership',
#                                            camp_name=f'BKwire Team Membership - {cust_email}',
#                                            email_address=cust_email,
#                                            list_name='BKwire Team Leader List - recycle',
#                                            html_temp_name='bkw_team_leader')
 #       cust_email = self.db1.fetch_no_cache(sql=f"SELECT email_address FROM users WHERE customer_id = 'cus_MTNzHHTEVDPnkK'")
 #       print(cust_email[0]['email_address'])
    def get_price_table_details(self, customer_id):
        si = StripeIntegrate()

        all_product_ids = si.get_product_ids()

        customer_id = customer_id
        user_subs = (si.get_user_subs(customer_id=customer_id))

        current_plan_id = (user_subs['data'][0]['items']['data'][0]['plan']['product'])
        #print(current_plan_id)
        for a in all_product_ids:
            if a['data'][0]['id'] == current_plan_id:
                a['data'][0]['isPurhcased'] = True
            else:
                a['data'][0]['isPurhcased'] = False

            price_id_amounts = (si.get_price_ids(product=a['data'][0]['id']))
            price_id_amounts = (price_id_amounts['data'][0]['unit_amount'])
            a['data'][0]['unit_amount'] = price_id_amounts
            for p in bkw_stripe_config.subscription_features_list:
                if p['product_name'] == a['data'][0]['metadata']['product_name']:
                    a['data'][0]['features'] = p['feature_list']
                    
        return all_product_ids
    
################TEST Fucntions#########################
    def test_downgrade(self, cust_id='cus_NS62NyIqxwlMyE', sub_price_level=1):
            # Check the current value before update - take necessary action on downgrades
            sql_query_user_info = f"SELECT * FROM users WHERE customer_id = '{cust_id}'"
            user_info_results = self.db1.fetch_no_cache(sql=sql_query_user_info)
            print(f"user_info: {user_info_results}")

            if sub_price_level < user_info_results[0]['subscription_price_level']:
                user_id = user_info_results[0]['id']
                # clear watchlist - as a user could add max watchlist and downgrade and still get notified
                sql_bk_del = f"DELETE FROM `bankruptcies_watchlist` WHERE user_id = {user_id};"
                self.db1.sql_commit(sql_bk_del)
                sql_comp_del = f"DELETE FROM `companies_watchlist` WHERE user_id = {user_id};"
                self.db1.sql_commit(sql_comp_del)

            if user_info_results[0]['subscription_price_level'] == 2 and sub_price_level != 2:
                # If the user was on Team Membership but no longer - need to remove team members access
                user_sql_query = f"SELECT * FROM user_team WHERE principal_email_address = '{user_info_results[0]['email_address']}'"
                user_team = self.db1.fetch_no_cache(sql=user_sql_query)
                sql_team_del = f"DELETE FROM user_team WHERE principal_email_address = '{user_info_results[0]['email_address']}'"
                self.db1.sql_commit(sql_team_del)

                for u in user_team:
                    team_member_email = u['member_email_address']
                    # update db with new sub info for user
                    sql_update = f"UPDATE users SET \
                    subscription_id = '', \
                    subscription_status = '', \
                    subscription_price_id = '', \
                    subscription_price_level = 0 \
                    WHERE email_address = '{team_member_email}'"

                    logger.debug(f"Stripe Webhook SQL_QUERY update user: {sql_update}")
                    self.db1.sql_commit(sql_update)            


def main():
    si = StripeIntegrate()
    si.test_downgrade()
#    all_product_ids = si.get_product_ids()
#
#    customer_id = 'cus_Mvz9gbRomjRvG7'
#    user_subs = (si.get_user_subs(customer_id=customer_id))
#
#    current_plan_id = (user_subs['data'][0]['items']['data'][0]['plan']['product'])
#    #print(current_plan_id)
#    for a in all_product_ids:
#        if a['data'][0]['id'] == current_plan_id:
#            a['data'][0]['isPurhcased'] = True
#        else:
#            a['data'][0]['isPurhcased'] = False
#
#        price_id_amounts = (si.get_price_ids(product=a['data'][0]['id']))
#
#        if len(price_id_amounts['data']) > 1:
#             for p in price_id_amounts:
#               if p['recurring']['interval'] == 'month':
#                   price_id_amounts = p['unit_amount']
#        else:
#            price_id_amounts = (price_id_amounts['data'][0]['unit_amount'])
#            
#        a['data'][0]['unit_amount'] = price_id_amounts
#
#        for p in bkw_stripe_config.subscription_features_list:
#            if p['product_name'] == a['data'][0]['metadata']['product_name']:
#                a['data'][0]['features'] = p['feature_list']
#
#    print(all_product_ids)


# MAIN
if __name__ == '__main__':
    main()
