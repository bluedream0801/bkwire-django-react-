import os,sys
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
from modules.database.bkw_db_functions import BkDbFunctions

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


def add_files_to_de_links():
    BKDBF = BkDbFunctions()
    sql_res_subv = BKDBF.fetch_no_cache(sql=f"SELECT DISTINCT case_id FROM `docket_table` WHERE activity LIKE '% subchapter v%';")
    for i in sql_res_subv:
        BKDBF.sql_insert_records(sql=f"UPDATE bankruptcy_filing_data SET sub_chapv = 1 WHERE dci_id = {i['case_id']}")

    #
    #BKDBF = BkDbFunctions()
    sql = f"SELECT bkw_files.id as bkw_id, bfd_id, file_type, name, bankruptcy_filing_data.dci_id as case_id FROM `bkw_files` \
    LEFT JOIN bankruptcy_filing_data ON bankruptcy_filing_data.id = bkw_files.bfd_id \
    WHERE file_type = 'petition' OR file_type = 'creditors'"

    sql_res_subv = BKDBF.fetch_no_cache(sql=sql)
    for i in sql_res_subv:
        file_link_split = i['name'].split('.')
        doc_url_number = file_link_split[3].split('-')
        sql_select_docket_act = f"SELECT id, doc_url, SUBSTRING_INDEX(activity, ' ',4) as sub_act, case_id FROM docket_table WHERE case_id = {i['case_id']}"
        sql_select_docket_act_results = BKDBF.fetch_no_cache(sql=sql_select_docket_act)
        if sql_select_docket_act_results:
            for s in sql_select_docket_act_results:
                if 'doc_url' in s and s['doc_url'] != None:
                    doc_url_link_num = s['doc_url'].split('/doc1/')
                    if doc_url_link_num[0] != 'None':
                        if doc_url_link_num[1] == doc_url_number[0]:
                            record_row_id = BKDBF.sql_insert_records(sql=f"INSERT INTO docket_entry_file_links (`docket_entry_id`, `docket_entry_name`, `docket_entry_link`, `docket_case_id`) VALUES ({s['id']}, '{s['sub_act']}', '{s['doc_url']}','{s['case_id']}')")
                            BKDBF.sql_insert_records(sql=f"UPDATE bkw_files SET de_file_links_id = {record_row_id} WHERE id = {i['bkw_id']}")
                        else:
                            print(f"not a match: {doc_url_link_num[1]} -- {doc_url_number[0]}")


def query_old_cases_add_to_de_links():
    BKDBF = BkDbFunctions()
    sql=f"SELECT bankruptcy_filing_data.dci_id AS case_id, bankruptcy_filing_data.dci_id AS bfd_id, bkw_files.id as bkw_id FROM `bkw_files` \
    JOIN bankruptcy_filing_data ON bankruptcy_filing_data.id = bkw_files.bfd_id \
    WHERE de_file_links_id IS NULL"

    sql_res_subv = BKDBF.fetch_no_cache(sql=sql)
    for i in sql_res_subv:
        #query_results = BKDBF.fetch_no_cache(sql=f"SELECT id, doc_url, SUBSTRING_INDEX(activity, ' ',4) as sub_act, case_id FROM docket_table WHERE case_id = {i['case_id']} AND activity LIKE '%petition%'")
        query_results = BKDBF.fetch_no_cache(sql=f"SELECT id FROM docket_entry_file_links WHERE docket_case_id = {i['case_id']}")
        if query_results:
            for s in query_results:
            #        if 'doc_url' in s and s['doc_url'] != None:
            #            record_row_id = BKDBF.sql_insert_records(sql=f"INSERT INTO docket_entry_file_links (`docket_entry_id`, `docket_entry_name`, `docket_entry_link`, `docket_case_id`) VALUES ({s['id']}, '{s['sub_act']}', '{s['doc_url']}','{s['case_id']}')")
                BKDBF.sql_insert_records(sql=f"UPDATE bkw_files SET de_file_links_id = {s['id']} WHERE bfd_id = {i['bfd_id']}")

#################################################################################
def main():
    #add_files_to_de_links()
    query_old_cases_add_to_de_links()


# MAIN
if __name__ == '__main__':
    main()


