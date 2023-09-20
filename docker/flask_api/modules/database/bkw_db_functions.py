#!/usr/bin/python

import re
import os,sys
import datetime
import logging.config
from logtail import LogtailHandler
import mysql.connector
import pandas as pd
import numpy as np
import xml.etree.ElementTree as ET
from os import path

### MODULE SETUP ###
sys.path.insert(0, '/app')
from modules.database import config

### LOGGING SETUP ###
handler = LogtailHandler(source_token=os.environ['LOG_SOURCE_TOKEN'])
#log_file_path = path.join(path.dirname(path.abspath(__file__)), '/app/logging/logging.ini')
#logging.config.fileConfig(log_file_path)
#logger = logging.getLogger(__name__)
#logger_list = ['boto', 'boto3', 'chardet', 'urllib3', 'botocore', 's3transfer', 'PIL']
#for i in logger_list:
#    logging.getLogger(i).setLevel(logging.CRITICAL) #sets all #logging sources to crit level

logger = logging.getLogger(__name__)
logger.handlers = []
logger.setLevel(logging.DEBUG) # Set minimal log level
logger.addHandler(handler) # asign handler to logger


### DATETIME SETUP ###
now  = datetime.datetime.now()
db_ts = now.strftime('%Y/%m/%d')
config_create_date = now.strftime('%Y%d%m%H%M%S')

class BkDbFunctions:
    def __init__(self):
        self.mysql_db_user = config.user
        self.mysql_db_password = config.password
        self.mysql_db_host = config.host
        self.mysql_db_database = config.database


    def db_login(self):
        try:
            conn = mysql.connector.connect(
            user=self.mysql_db_user,
            password=self.mysql_db_password,
            host=self.mysql_db_host,
            database=self.mysql_db_database,
            auth_plugin='mysql_native_password')
        except Exception as e:
            logging.error(f"MySQL Database Login Failed: {e}")
            conn = False
        finally:
            self.db_cnxn = conn
            return(conn)


    def fetch_no_cache(self, sql):
        self.db_login()
        cursor = self.db_cnxn.cursor(dictionary=True)
        try:
            cursor.execute(sql)
            data = cursor.fetchall()
        except Exception as e:
            logging.error(e)
            data = False
        finally:
            return(data)


    def fetch_execute_like(self, sql, var):
        cursor = self.db_cnxn.cursor(dictionary=True)
        var = f"%{var}%"
        try:
            cursor.execute(sql, [var])
            data = cursor.fetchall()            
        except Exception as e:
            logging.error(f"MySQL Database fetch_execute_like Failed: {e}")
            data = False
        finally:
            return data


    def fetch_execute(self, sql, var):
        self.db_login()
        cursor = self.db_cnxn.cursor(dictionary=True)
        try:
            cursor.execute(sql, [var])
            data = cursor.fetchall()
        except Exception as e:
            logging.error(f"MySQL Database fetch_execute Failed: {e}")
            data = False
        finally:
            return data


    def sql_insert_records(self, sql):
        logging.debug(sql)
        cursor = self.db_cnxn.cursor(dictionary=True)
        try:
            cursor.execute(sql)
            self.db_cnxn.commit()
            msg = cursor.lastrowid
        except Exception as e:
            logging.error(f"MySQL Database sql_insert_records Failed: {e}")
            msg = False
        finally:
            return msg


    def sql_transaction_insert(self, sql):
        logging.debug(sql)
        cursor = self.db_cnxn.cursor(dictionary=True)
        try:
            cursor.execute(sql)
            msg = True
        except Exception as e:
            logging.error(f"MySQL Database sql_transaction_insert Failed: {e}")
            msg = False
        finally:
            return msg


    def insert_companies(self, **kwargs):
        #Debtor Company Information table
        self.db_login()
        cursor = self.db_cnxn.cursor
        for i in kwargs['pacer_data_results']:
            if kwargs['pacer_data_results'][i]['comp_exists'] == False:
                company_slug = kwargs['pacer_data_results'][i]['comp_slug']
                try:
                    sql = """INSERT INTO companies (name, slug)
                    VALUES (%s,%s)"""
                    val = (i, company_slug)
                    cursor.execute(sql, val)
                    self.db_cnxn.commit()
                    # Get ID of db entry for later use
                    logging.debug(f'MYSQL insert_companies Successful: {i}')
                except Exception as e:
                    logging.error(f"MYSQL insert_companies Failed: {e}, {i}")
            else:
                logging.debug(f'MYSQL insert_companies Company Exists: {i}')


    def insert_docket_data(self, pacer_data_results):
        #Docket Information Table
        cursor = self.db_cnxn.cursor()
        for k in pacer_data_results:
            check_result_case = None
            logger.debug(f"pacer tuple: {pacer_data_results[k]['dci_id']}")
            try:
                check_result_case = self.check_if_case_exists(pacer_data_results[k]['dci_id'], pacer_data_results[k]['case_number'])
            except Exception as e:
                logger.error('check_if_case_exists ddata FAILED' + str(e))
                
            if check_result_case is not None and 'docket_table' in pacer_data_results[k]:
                if not pacer_data_results[k]['docket_table'] is None:
                    for dt in pacer_data_results[k]['docket_table']:
                        try:
                            entry_date,doc_url,pages,docs,activity,extra_docs = dt.split(':bkwsplit:')
                            sql = """INSERT INTO docket_table (entry_date, pages, docs, doc_url, activity, case_id)
                            VALUES (%s,%s,%s,%s,%s,%s)"""
                            val = (entry_date, pages, docs, doc_url, activity.strip(), pacer_data_results[k]['dci_id'])
                            cursor.execute(sql, val)
                            self.db_cnxn.commit()

                            pacer_data_results[k]['docket_files_link_id'] = cursor.lastrowid
                        except Exception as e:
                            logger.warning(f"docket_table commit FAILED: {e} {k}")
                        else:
                            logger.debug('MySQL: docket_table commit successful!')
                            #check
                else:
                    logger.debug('MSYQL: docket_table is None')
            else:
                logger.debug('existing case data does not exist: ' + str(check_result_case))


    def check_if_case_exists(self, bfd_id, case_num):
        cursor = self.db_cnxn.cursor()
        sql = """SELECT id FROM bankruptcy_filing_data WHERE dci_id = %s"""
        logger.debug(f"{sql}, {bfd_id}")
        cursor.execute(sql, [bfd_id])
        row = cursor.fetchone()
        if not row is None:
            try:
                logger.debug("bankruptcy filing id = " + str(row[0]))
                logger.debug("bankruptcy filing exists in db: " + str(bfd_id))
                data = str(row[0])
            except:
                logger.debug("Unable to find db_id: " + str(bfd_id))
                data = None
        else:
            logger.debug("bankruptcy filing does NOT exist: " + str(bfd_id))
            data = None

        return data
                #comp_scrub = company_scrub(i)
            #case_title_comp = title(comp_scrub)
            #company_slug = create_company_slug(comp_scrub)
            #check_result_company = check_if_company_exists(cnxn, company_slug)
            #ncode_id = look_naics_key(ncode)
#            if check_result_company is False:
#                try:
#                    sql = """INSERT INTO companies (name, slug, industry_id)
#                    VALUES (%s,%s,%s)"""
#                    val = (case_title_comp, company_slug, ncode_id)
#                    cursor.execute(sql, val)
#                    cnxn.commit()
#                    # Get ID of db entry for later use
#                    logging.debug('MySQL: companies commit successful!')
#                except Exception as e:
#                    logging.warning('companies commit FAILED: ' + str(e))
#                    logging.debug(company_name)
#                    pass
#            else:
#                logging.debug('companies commit exists: ' + company_name)
#        cursor.close()
#    def sql_commit_w_values(self, sql, vals):
#        """
#        This module expects the 
#        sql = "INSERT INTO bankruptcy_204206_data (creditor_name, creditor_zip) VALUES (%s,%s)"
#        vals = (cred_title_comp, unsecured_creditor_zip)
#        """
#        logging.debug(sql)
#        cursor = self.db_cnxn.cursor(dictionary=True)
#        try:
#            cursor.execute(sql, vals)
#            self.db_cnxn.commit()
#            return('Success')
#        except Exception as e:
#            logging.error(e)
#            return('Failed')
#
#    def sql_commit_many(self, sql, data):
#        logging.debug(sql)
#        cursor = self.db_cnxn.cursor(dictionary=True)
#        try:
#            cursor.executemany(sql, data)
#            self.db_cnxn.commit()
#            return('Success')
#        except Exception as e:
#            logging.error(e)
#            self.db_cnxn.rollback()
#            return('Failed')
#
#    def create(self, kwargs):
#        cursor = self.db_cnxn.cursor(dictionary=True)
#        try:
#            sql = """INSERT INTO users (first_name, last_name, email_address, phone_number, company_sector, company_name, company_state, company_zip_code, email_alerts_enabled, email_alert_1, email_alert_2, email_alert_3, text_alerts_enabled, phone_alert_1, phone_alert_2, phone_alert_3)
#            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""
#            val = [(kwargs['first_name'], kwargs['last_name'], kwargs['email_address'], kwargs['phone_number'], kwargs['company_sector'], kwargs['company_name'], kwargs['company_state'], kwargs['company_zip_code'], kwargs['email_alerts_enabled'], kwargs['email_alert_1'], kwargs['email_alert_2'], kwargs['email_alert_3'], kwargs['text_alerts_enabled'], kwargs['phone_alert_1'], kwargs['phone_alert_2'], kwargs['phone_alert_3'])]
#            cursor.executemany(sql, val)
#            self.db_cnxn.commit()
#            logging.debug('User creation successful')
#            return('Success', cursor.lastrowid)
#        except Exception as e:
#            logging.error(e)
#            return(e)
#
#    def create_team_user(self, pemail, memail):
#        cursor = self.db_cnxn.cursor(dictionary=True)
#        try:
#            sql = """INSERT INTO user_team (principal_email_address, member_email_address)
#            VALUES (%s,%s)"""
#            val = [(pemail, memail)]
#            cursor.executemany(sql, val)
#            self.db_cnxn.commit()
#            logging.debug('Team user creation successful')
#            return('Team user creation successful')
#        except Exception as e:
#            logging.error(f"Team user creation failed: {e}")
#            return(f"Team user creation failed: {e}")
#    #MOVED THIS TO JINJA2 template
#    #def update(self, first_name=None, last_name=None, email_address=None, phone_number=None, company_name=None, company_address=None, company_zip_code=None, email_alerts_enabled=None, email_alert_1=None, email_alert_2=None, email_alert_3=None, text_alerts_enabled=None, phone_alert_1=None, phone_alert_2=None, phone_alert_3=None):
#
#    def sql_delete_team_user(self, pemail, memail):
#        cursor = self.db_cnxn.cursor(dictionary=True)
#        sql = f"DELETE FROM user_team WHERE principal_email_address = \'{pemail}\' AND member_email_address = \'{memail}\'"
#        sql_user_table = f"UPDATE users SET subscription_id=NULL, subscription_price_id=NULL, subscription_status=NULL, \
#            subscription_inherited=0, subscription_level=0 WHERE email_address = \'{memail}\'"
#        #DELETE FROM sql_table WHERE condition;
#        try:
#            cursor.execute(sql)
#            cursor.execute(sql_user_table)
#            self.db_cnxn.commit()
#            self.db_cnxn.close()
#            return('Removed team member successfully')
#        except Exception as e:
#            logging.error(e)
#            return('Removed team member failed')


#################################################################################
def main():
    pass


# MAIN
if __name__ == '__main__':
    main()


