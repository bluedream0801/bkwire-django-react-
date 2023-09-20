import re
import json
import requests
import os, glob, sys
from os import path
import pandas as pd
import logging.config
from logtail import LogtailHandler
import shortuuid
import pandas as pd
from datetime import datetime, timedelta, date
from jinja2 import Environment, FileSystemLoader, escape
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

## IMPORT CUSTOM MODULES ##
sys.path.insert(0, '/app')
from modules.database.bkw_db_functions import BkDbFunctions
from modules.pacer.pacer_api_v3 import PacerApiSvc
from modules.parsers.bkw_data_parser import BkDataParser
from modules.parsers.parse_results_download_pdf_v2 import ResultsParserSvc
#from modules.company.bkw_company_functions import BkCompFunctions
from modules.database.db_select import dbSelectItems
from modules.aws.textrac_v3 import convert_pdf2png, rawtext_pdffromimage, parse_204, parse_206
from modules.database.db_connec_v2 import insert_data_b204206d, insert_companies, insert_data_additional_info, \
     insert_files_inventory, insert_docket_entry_file
from modules.aws.textrac_v3 import convert_pdf2png, rawtext_pdffromimage, get_naics_code, parse_201, parse_204, \
    parse_205, parse_206, delete_file, copy_object, upload_file

### LOGGING SETUP ###
handler = LogtailHandler(source_token=os.environ['LOG_SOURCE_TOKEN'])
logger = logging.getLogger(__name__)
logger.handlers = []
logger.setLevel(logging.DEBUG) # Set minimal log level
logger.addHandler(handler) # asign handler to logger

class BkAppFunctions:
    def __init__(self):
        pass


    def pacer_login_session(self):
        self.p1 = PacerApiSvc()
        pacer_login_session = None
        cookie_check = self.p1.pacer_check_cookie()
        if cookie_check == 'expired':
            pacer_login_session = self.p1.pacer_session_login()
            self.p1.pacer_cso_login()
        else:
            logger.debug('cookies still valid')
            pacer_login_session = cookie_check

        if pacer_login_session == None:
            logger.error(f"PACER login session failed: {pacer_login_session}")
            exit(1)
        self.p2 = ResultsParserSvc(pacer_login_session)
        return(self.p1)


    def case_existing_check(self, pacer_data_results, case_refresh_trigger=False):
        remove_list = []
        BKDF = BkDbFunctions()
        BKCF = BkCompFunctions()
    
        for key in pacer_data_results:
            comp_exists = False
            case_exists = False

            comp_slug = BKCF.create_company_slug(key)
            pacer_data_results[key]['comp_slug'] = comp_slug

            sql_query_comp = """SELECT * FROM companies WHERE slug = %s"""
            sqlq_results_comp = BKDF.fetch_execute(sql_query_comp, comp_slug)

            if sqlq_results_comp:
                comp_exists = True
                sql_query_case = """SELECT * FROM bankruptcy_filing_data WHERE case_number = %s"""
                sqlq_results_case = BKDF.fetch_execute(sql_query_case, pacer_data_results[key]['case_number'])

                if sqlq_results_case:
                    case_exists = sql_query_case[0]['id']
                    logger.debug(f"MYSQL case_existing_check Found: {sqlq_results_case}")
                    pacer_data_results[key]['status_201'] = sqlq_results_case[0]['status_201']
                    pacer_data_results[key]['status_204206'] = sqlq_results_case[0]['status_204']

            pacer_data_results[key]['comp_exists'] = comp_exists
            pacer_data_results[key]['case_exists'] = case_exists

        return pacer_data_results


    def parse_pacer_results(self, **kwargs):
        self.pacer_login_session()
        remove_list = []
        data_201_results = {}
        pacer_pdf_results = {}
        data_204206_results = {}
        data_204206_list_keys = []

        html_soup = None
        session_drm  = None
        html_tables_results = None

        for k in kwargs['pacer_data_results']:
            session_drm = self.p1.pacer_dkt_rpt(kwargs['pacer_data_results'][k]['case_link'])
            if session_drm != None and session_drm != False:
                html_soup, pacer_user_session  = self.p1.pacer_post_for_html(kwargs['pacer_data_results'][k]['case_link'], kwargs['pacer_data_results'][k]['case_number'], kwargs['pacer_data_results'][k]['date_filed'])
            else:
                pass

            if html_soup != None and html_soup != False:
                html_tables_results = self.p2.parse_html_table_to_dic(k, html_soup)
            elif html_soup == 0:
                html_soup, pacer_user_session = self.p1.pacer_post_for_html(kwargs['pacer_data_results'][k]['case_link'], kwargs['pacer_data_results'][k]['case_number'], kwargs['pacer_data_results'][k]['date_filed'])
                html_tables_results = self.p2.parse_html_table_to_dic(k, html_soup)
            else:
                pass
            if html_tables_results != None and html_tables_results != False:
                try:
                    self.p2.docket_table_parse(html_tables_results[k]['docket_table'], kwargs['pacer_data_results'], k)
                    #kwargs['pacer_data_results'][k]['docket_table'] = object_name
                except Exception as e:
                    logger.error(f"docket_table_parse failed: {e} -- {k}")
            else:
                pass

        if html_tables_results != None and html_tables_results != False:
            try:
                self.p2.parse_docket_entries(html_tables_results, kwargs['pacer_data_results'], pacer_pdf_results)
            except Exception as e:
                logger.error(f"parse_docket_entries failed: {e} -- {k}")
        else:
            pass

        return (kwargs['pacer_data_results'], pacer_pdf_results)


    def parse_204206_meta_data(self, pacer_data_results, pacer_pdf_results):
        logger.debug(f"parse_204206_meta_data triggered")
        data_204206_results = {}

        for k in pacer_data_results:
            pacer_data_results[k]['case_name'] = k
            pacer_data_results[k]['parse_204206_attempt'] = False

            for pdf_results_key in pacer_pdf_results[k]:
                png_list = convert_pdf2png(pdf_results_key, 'bpwa.parse-png', pacer_pdf_results[k][pdf_results_key])

                for p in png_list:
                    documentName = p
                    s3BucketName = 'bpwa.parse-png'                        
                    pdf_png_meta_data = rawtext_pdffromimage(p, s3BucketName)

                    if 'official form 204' in pdf_png_meta_data:
                        pacer_data_results[k]['parse_204206_attempt'] = True
                        data_204206_results = parse_204(s3BucketName, documentName, pacer_data_results, k)
                    if 'top unsecured creditors' in pdf_png_meta_data:
                        pacer_data_results[k]['parse_204206_attempt'] = True
                        data_204206_results = parse_204(s3BucketName, documentName, pacer_data_results, k)
                    if 'amount of unsecured claim' in pdf_png_meta_data:
                        pacer_data_results[k]['parse_204206_attempt'] = True
                        data_204206_results = parse_204(s3BucketName, documentName, pacer_data_results, k)
                    if 'priority creditor\'s name and mailing address' in pdf_png_meta_data:
                        pacer_data_results[k]['parse_204206_attempt'] = True
                        data_204206_results = parse_206(pdf_png_meta_data, documentName, pacer_data_results, k)
                    if 'nonpriority creditor\'s name and mailing address' in pdf_png_meta_data:
                        pacer_data_results[k]['parse_204206_attempt'] = True
                        data_204206_results = parse_206(pdf_png_meta_data, documentName, pacer_data_results, k)
                    else:
                        pass

                    ## CLEAN UP
                    try:
                        cleanup_png_file = 'results/pdf2png/' + p
                        os.remove(cleanup_png_file)
                    except Exception as e:
                        logger.error(f"file cleanup error: {e}")

        return pacer_data_results, data_204206_results
    
    def insert_204206_meta_data(data_204206_results):
        data_204206_list_keys = []

        for k in data_204206_results:
            for kv in data_204206_results[k]:
                if kv.strip() in data_204206_list_keys:
                    try:
                        comp_name_fixed = re.split('(?i)attn:|attn|c\/o|esq\.|esq|esquire',data_204206_results[k][kv]['creditor_company_name'])
                        if len(comp_name_fixed) > 0:
                            logger.debug(f"data_204206 comp_name_fixed: found")
                            insert_companies(connection, comp_name_fixed[0].strip(), data_204206_results[k][kv]['industry'])
                            insert_data_b204206d(connection, comp_name_fixed[0].strip(), data_204206_results[k][kv]['creditor_company_zip'], data_204206_results[k][kv]['nature_of_claim'].upper(), data_204206_results[k][kv]['unsecured_claim_value'], data_204206_results[k][kv]['industry'], pacer_data_results[k]['bkw_filing_id'])
                        else:
                            insert_companies(connection, data_204206_results[k][kv]['creditor_company_name'].strip(), data_204206_results[k][kv]['industry'])
                            insert_data_b204206d(connection, data_204206_results[k][kv]['creditor_company_name'].strip(), data_204206_results[k][kv]['creditor_company_zip'], data_204206_results[k][kv]['nature_of_claim'].upper(), data_204206_results[k][kv]['unsecured_claim_value'], data_204206_results[k][kv]['industry'], pacer_data_results[k]['bkw_filing_id'])
                    except Exception as e:
                        pass # No need to log this, expected exceptions
                    # Parse Phone/Email from Creditors
                    joined_string_phone = None
                    joined_string_email = None
                    try:
                        comp_name_fixed = re.split('(?i)attn:|attn|c\/o|esq\.|esq|esquire',data_204206_results[k][kv]['creditor_company_name'])
                        if len(data_204206_results[k][kv]['creditor_email']) > 0:
                            joined_string_phone = ",".join(data_204206_results[k][kv]['creditor_phone'])
                        if len(data_204206_results[k][kv]['creditor_phone']) > 0:
                            joined_string_email = ",".join(data_204206_results[k][kv]['creditor_email'])
                            if len(comp_name_fixed) > 0:
                                insert_data_additional_info(connection, comp_name_fixed[0].strip(), joined_string_email, joined_string_phone, pacer_data_results[k]['bkw_filing_id'])
                            else:
                                insert_data_additional_info(connection,  data_204206_results[k][kv]['creditor_company_name'].strip(), joined_string_email, joined_string_phone, pacer_data_results[k]['bkw_filing_id'])
                        else:
                            pass
                    except Exception as e:
                        pass
                else:
                    pass        


    def parse_201_meta_data(self, pacer_data_results, pacer_pdf_results):
        logger.debug(f"parse_201_meta_data triggered")
        data_204206_results = {}

        for k in pacer_data_results:
            pacer_data_results[k]['case_name'] = k
            pacer_data_results[k]['parse_201_attempt'] = False

            for pdf_results_key in pacer_pdf_results[k]:
                png_list = convert_pdf2png(pdf_results_key, 'bpwa.parse-png', pacer_pdf_results[k][pdf_results_key])

                for p in png_list:
                    documentName = p
                    s3BucketName = 'bpwa.parse-png'                        
                    pdf_png_meta_data = rawtext_pdffromimage(p, s3BucketName)

                    if 'page 2'in pdf_png_meta_data and 'official form 201' in pdf_png_meta_data:
                        naics_code_results = get_naics_code(pdf_png_meta_data, p)
                        try:
                            if naics_code_results != None:
                                data_201_results[k]['naics_code'] = naics_code_results
                            else:
                                pass
                        except Exception as e:
                            pass

                    if 'page 3' in pdf_png_meta_data and 'official form 201' in pdf_png_meta_data:
                        pacer_data_results[k]['parse_201_attempt'] = True
                        data_201_results = parse_201(s3BucketName, documentName, data_201_results, k, pg='3')

                    if 'page 4' in pdf_png_meta_data and 'official form 201' in pdf_png_meta_data:
                        pacer_data_results[k]['parse_201_attempt'] = True
                        data_201_results.update(parse_201(s3BucketName, documentName, data_201_results, k, pg='4'))

                    if 'official form 205' in pdf_png_meta_data:
                            data_201_results[k]['estimated_assets_min'] = -1
                            data_201_results[k]['estimated_assets_max'] = -1
                            data_201_results[k]['estimated_creditors_min'] = -1
                            data_201_results[k]['estimated_creditors_max'] = -1                                            
                            data_201_results[k]['estimated_liabilities_min'] = -1
                            data_201_results[k]['estimated_liabilities_max'] = -1
                            pacer_data_results[k]['naics_code'] = 511
                            pacer_data_results[k]['involuntary'] = 1
                            pacer_data_results[k]['status_201'] = 1
                            pacer_data_results[k]['status_204206'] = 1

                    ## CLEAN UP
                    try:
                        cleanup_png_file = 'results/pdf2png/' + p
                        os.remove(cleanup_png_file)
                    except Exception as e:
                        logger.error(f"file cleanup error: {e}")

        return pacer_data_results, data_201_results        


    def format_insert_creditor_data(self, pacer_data_results, data_204206_results):
            
        db1 = dbSelectItems()
        connection = db1.db_login()

        data_204206_list_keys = []
        pacer_data_results_204_key_list = ['court_id', 'cs_year', 'cs_number', 'cs_office', 'cs_chapter', 'date_filed', 'case_number', 'case_link',\
            'pdf_dl_status_201', 'pdf_dl_status_204206', 'pdf_dl_skip', 'company_city', 'company_state', 'company_zip', 'company_industry', 'company_address',\
            'pdf_filename', 'case_name']
            
        # Remove 204 data if no case data found
        for key in data_204206_results:
            for dk in data_204206_results[key]:
                if dk in pacer_data_results_204_key_list:
                    pass
                else:
                    data_204206_list_keys.append(dk.strip())

        insert_docket_entry_file('connection', pdr=pacer_data_results)
        insert_files_inventory(pacer_data_results)
        # Fix up and Insert Unsecured Data
        for k in data_204206_results:
            for kv in data_204206_results[k]:
                if kv.strip() in data_204206_list_keys:
                    try:
                        comp_name_fixed = re.split('(?i)attn:|attn|c\/o|esq\.|esq|esquire',data_204206_results[k][kv]['creditor_company_name'])
                        if len(comp_name_fixed) > 0:
                            logger.debug(f"data_204206 comp_name_fixed: found")
                            insert_companies('connection', comp_name_fixed[0].strip(), data_204206_results[k][kv]['industry'])
                            insert_data_b204206d('connection', comp_name_fixed[0].strip(), data_204206_results[k][kv]['creditor_company_zip'], data_204206_results[k][kv]['nature_of_claim'].upper(), data_204206_results[k][kv]['unsecured_claim_value'], data_204206_results[k][kv]['industry'], pacer_data_results[k]['bkw_filing_id'])
                        else:
                            insert_companies('connection', data_204206_results[k][kv]['creditor_company_name'].strip(), data_204206_results[k][kv]['industry'])
                            insert_data_b204206d('connection', data_204206_results[k][kv]['creditor_company_name'].strip(), data_204206_results[k][kv]['creditor_company_zip'], data_204206_results[k][kv]['nature_of_claim'].upper(), data_204206_results[k][kv]['unsecured_claim_value'], data_204206_results[k][kv]['industry'], pacer_data_results[k]['bkw_filing_id'])
                    except Exception as e:
                        pass # No need to log this, expected exceptions
                    # Parse Phone/Email from Creditors
                    joined_string_phone = None
                    joined_string_email = None
                    try:
                        comp_name_fixed = re.split('(?i)attn:|attn|c\/o|esq\.|esq|esquire',data_204206_results[k][kv]['creditor_company_name'])
                        if len(data_204206_results[k][kv]['creditor_email']) > 0:
                            joined_string_phone = ",".join(data_204206_results[k][kv]['creditor_phone'])
                        if len(data_204206_results[k][kv]['creditor_phone']) > 0:
                            joined_string_email = ",".join(data_204206_results[k][kv]['creditor_email'])
                            if len(comp_name_fixed) > 0:
                                insert_data_additional_info('connection', comp_name_fixed[0].strip(), joined_string_email, joined_string_phone, pacer_data_results[k]['bkw_filing_id'])
                            else:
                                insert_data_additional_info('connection',  data_204206_results[k][kv]['creditor_company_name'].strip(), joined_string_email, joined_string_phone, pacer_data_results[k]['bkw_filing_id'])
                        else:
                            pass
                    except Exception as e:
                        pass
                else:
                    pass


    def build_pacer_data_from_sql(self, mysql_data_sets):
        logger.debug(f"build_pacer_data_from_sql triggered")

        status_201 = None
        status_204 = None
        pacer_data_results = {}

        for m in mysql_data_sets:
            pacer_data_results[m['case_name']] = {}
            pacer_data_results[m['case_name']]['case_number'] = m['case_number']
            pacer_data_results[m['case_name']]['court_id'] = m['court_id']
            pacer_data_results[m['case_name']]['date_filed'] = m['date_filed']
            pacer_data_results[m['case_name']]['cs_number'] = m['cs_number']
            pacer_data_results[m['case_name']]['cs_office'] = m['cs_office']
            pacer_data_results[m['case_name']]['cs_chapter'] = m['cs_chapter']
            pacer_data_results[m['case_name']]['case_link'] = m['case_link']
            pacer_data_results[m['case_name']]['pdf_dl_status'] = None
            pacer_data_results[m['case_name']]['bkw_filing_id'] = m['id']
            pacer_data_results[m['case_name']]['dci_id'] = m['dci_id']
            # this is dumb - needs fixed
            if m['status_201'] == 1:
                status_201 = 'complete'
            else:
                status_201 = 'incomplete'
            if m['status_204206'] == 1:
                status_204 = 'complete'
            else:
                status_204 = 'incomplete'

            pacer_data_results[m['case_name']]['pdf_dl_status_201'] = status_201
            pacer_data_results[m['case_name']]['pdf_dl_status_204'] = status_204
            pacer_data_results[m['case_name']]['parse_201_attempt'] = status_201
            pacer_data_results[m['case_name']]['parse_204206_attempt'] = status_204
            
        return pacer_data_results


def main():
    BAF = BkAppFunctions()
    BAF.pacer_login_session()
    #BAF.case_refresh(guid=245, dci_id=[6428])

# MAIN
if __name__ == '__main__':
    main()
