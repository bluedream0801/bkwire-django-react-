#!/usr/bin/python
# -*- coding: latin-1 -*-
from pyparsing import (
    makeHTMLTags,
    commonHTMLEntity,
    replaceHTMLEntity,
    htmlComment,
    anyOpenTag,
    anyCloseTag,
    LineEnd,
    replaceWith,
)
import copy
import re
import os
import sys
import pprint
import pyparsing
from os import path
import urllib.request
import logging.config
from logtail import LogtailHandler
import lxml.html as lh
import pandas as pd
from lxml import etree
import xml.etree.ElementTree as ET
from time import strptime
from bs4 import BeautifulSoup
from datetime import date, timedelta

### MODULE SETUP ###
sys.path.insert(0, '/app')
from modules.aws.check_s3_objects import check_against_s3_objects, download_pdf_from_s3_archive
from modules.address.international_parser import IntAddyParse

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

# Setup globals
xmlDict = {}
pdf_dl_pages = []
html_table_dic = {}
pp = pprint.PrettyPrinter(indent=4)
today = date.today().strftime('%m/%d/%Y')
class BkDataDownload:
    def __init__(self, pls):
        logger.info('Init results parser')
        self.pacer_login_session = pls

    def download_pdf_href(self, href_link, case_link, pdf_fn, pacer_data_results, key, pacer_pdf_results):
        """
        Attempt several stratgies to get PDF content from PACER
        1 - POST to the PDF URL
        2 - Payload sends back more HTML - we follow temp link for download
        3 - PDF content is in the HTML response and we strip non pdf chars and save
        """
        # Parse Vars
        response = None
        pdf_dl_dir = 'results/'
        split_case_link = case_link.split('?')
        split_case_link_slash = case_link.split('/')
        header_params = {'content-type':'application/x-www-form-urlencoded','referer':href_link}
        dl_pdf_data = {'caseid': split_case_link[1], 'got_receipt': 1, 'pdf_header': 1}
        # PDF Vars
        pdf_file_name = pdf_fn
        pdf_full_path_wname = pdf_dl_dir + pdf_file_name
        logger.info('Running POST: ' + href_link + ' caseID: ' + split_case_link[1])

        try: #Using all our parsing to this point - attempt to Download PDF
            response = self.pacer_login_session.post(href_link, headers=header_params, data=dl_pdf_data, timeout=20, allow_redirects=True)
            logger.debug('response: ' + str(response))
        except Exception as e:
            logger.info('PDF POST Request FAILED! ' + href_link)
            logger.error(e)
        # Check response for PDF content
        if response != None:
            pdf_content = re.search(r"%PDF", response.text)
            if pdf_content:
                pdf_contents = self.scrub_html_from_content(response)
                self.pdf_from_content(pdf_contents, pdf_full_path_wname, pacer_data_results, key, pacer_pdf_results, pdf_file_name)
            # if our results were more HTML - follow link to download PDF from temp URL
            elif response != None and response.headers['content-type'] == 'text/html; charset=UTF-8':
                self.download_pdf_from_temp_url(response, case_link, pdf_full_path_wname, pdf_fn, pacer_data_results, key, pacer_pdf_results)
            # if the response was actual PDF bytes - parse the pdf content
            elif response != None and response.headers['content-type'] == 'application/pdf':
                self.pdf_from_content(response, pdf_full_path_wname, pacer_data_results, key, pacer_pdf_results, pdf_file_name)
            else:
                logger.info('FAILED: Response is None or Unknown type' + str(href_link))
        else:
            logger.info('Request response was False')
            pass


    def download_pdf_files(self, file_link, file_w_path, doc_url_split):
        """
        Attempt several stratgies to get PDF content from PACER
        1 - POST to the PDF URL
        2 - Payload sends back more HTML - we follow temp link for download
        3 - PDF content is in the HTML response and we strip non pdf chars and save
        """
        # Parse Vars
        response = None
        header_params = {'content-type':'application/x-www-form-urlencoded','referer':file_link}
        dl_pdf_data = {'got_receipt': 1, 'pdf_header': 1}

        try: #Using all our parsing to this point - attempt to Download PDF
            response = self.pacer_login_session.post(file_link, headers=header_params, data=dl_pdf_data, timeout=20, allow_redirects=True)
            logger.debug('response: ' + str(response))
        except Exception as e:
            logger.info('PDF POST Request FAILED! ' + file_link)
            logger.error(e)
            return 'File Download Failed'
        # Check response for PDF content
        if response != None:
            pdf_content = re.search(r"%PDF", response.text)
            if pdf_content:
                pdf_contents = self.scrub_html_from_content(response)
                self.pdf_from_content_no_opt(pdf_contents, file_w_path)
            # if our results were more HTML - follow link to download PDF from temp URL
            elif response != None and response.headers['content-type'] == 'text/html; charset=UTF-8':
                self.download_pdf_from_temp_url_no_opt(response, file_w_path, doc_url_split)
            # if the response was actual PDF bytes - parse the pdf content
            elif response != None and response.headers['content-type'] == 'application/pdf':
                self.pdf_from_content_no_opt(response, file_w_path)
            else:
                logger.info('FAILED: Response is None or Unknown type' + str(file_link))
                return 'File Download Failed'
        else:
            logger.info('Request response was False')
            return 'File Download Failed'
        return 'File Download Success'

    def scrub_html_from_content(self, r):
        """
        This function will strip non PDF elements from response payload
        """
        pdf_elements = b"(%PDF.+(?s).+%%EOF)"
        rdi = re.search(pdf_elements, r.content)
        if rdi:
            return(rdi.group(0))
        else:
            return(r)

    def download_pdf_from_temp_url(self, r, case_link, pdf_full_path_wname, pdf_fn, pacer_data_results, key, pacer_pdf_results):
        pdf_temp_url = None
        pdf_doc_files = []
        pacer_data_results[key]['pdf_filename'] = None
        split_case_link_slash = case_link.split('/')

        soup = BeautifulSoup(r.content, 'lxml')
        tags = soup.find_all('a')
        for t in tags:
            try:
                s = re.search(r"(.+show_temp.+)", str(t.attrs['href']))
                v = re.search(r"doc1", str(t.attrs['href']))
                if s:
                    pdf_temp_url = 'https://' + split_case_link_slash[2] + s.group(0)
                    try:
                        logger.info('Running POST(download) from temp_url: ' + pdf_temp_url)
                        urllib.request.urlretrieve(pdf_temp_url, pdf_full_path_wname)
                        if re.search('petition',pdf_full_path_wname):
                            pacer_data_results[key]['pdf_dl_status_201'] = 'complete'
                            pacer_data_results[key]['pdf_filename_201'] = pdf_fn
                            #pdf_doc_files.append({'filename': pdf_full_path_wname, 'link': docket_entry_parse_url[1]})
                            #pacer_data_results[key]['pdf_doc_files'] = pdf_doc_files
                        else:
                            pacer_data_results[key]['pdf_dl_status_204206'] = 'complete'
                            pacer_data_results[key]['pdf_filename_204206'] = pdf_fn
                            #pdf_doc_files.append(pdf_fn)
                            #pacer_data_results[key]['pdf_doc_files'] = pdf_doc_files                            
                    except Exception as e:
                        logger.warning(f'FAILED: Unable to download PDF: {pdf_temp_url} : {e}')
                elif v:
                    if t.text == '1':
                        pdf_nested_href = 'https://' + split_case_link_slash[2] + t.attrs['href']
                        logger.info('Running POST(download) from temp_url: ' + pdf_nested_href)
                        self.download_pdf_href(pdf_nested_href, case_link, pdf_fn, pacer_data_results, key, pacer_pdf_results)
                else:
                    pass
            except Exception as e:
                logger.warning(f'temp_url download failed: {e}, trying something else..')
                pass

    def pdf_from_content(self, r, path_file_name, pacer_data_results, key, pacer_pdf_results, file_name):
        logger.info('Downloading PDF from content response')
        pdf_doc_files = []
        pacer_data_results[key]['pdf_filename'] = None
        #logger.info(r.text)
        try:
            with open(path_file_name, 'wb') as f:
                f.write(r)
            f.close()
            if re.search('petition',file_name):
                pacer_data_results[key]['pdf_dl_status_201'] = 'complete'
                pacer_data_results[key]['pdf_filename_201'] = file_name
               # pdf_doc_files.append(file_name)
               # pacer_data_results[key]['pdf_doc_files'] = pdf_doc_files                
            else:
                pacer_data_results[key]['pdf_dl_status_204206'] = 'complete'
                pacer_data_results[key]['pdf_filename_204206'] = file_name
               # pdf_doc_files.append(file_name)
               # pacer_data_results[key]['pdf_doc_files'] = pdf_doc_files 
        except Exception as e:
            logger.info('Downloading PDF from content FAILED')
            logger.error(e)


    def download_pdf_from_temp_url_no_opt(self, contents, file_w_path, doc_url_split):
        pdf_temp_url = None
        
        soup = BeautifulSoup(contents.content, 'lxml')
        tags = soup.find_all('a')
        for t in tags:
            try:
                s = re.search(r"(.+show_temp.+)", str(t.attrs['href']))
                v = re.search(r"doc1", str(t.attrs['href']))
                if s:
                    pdf_temp_url = f"{doc_url_split}{s.group(0)}"
                    try:
                        logger.info('Running (download) from temp_url: ' + pdf_temp_url)
                        urllib.request.urlretrieve(pdf_temp_url, file_w_path)                       
                    except Exception as e:
                        logger.warning(f'FAILED: Unable to download PDF: {pdf_temp_url} : {e}')
                        return 'File Download Failed'
                elif v:
                    if t.text == '1':
                        pdf_nested_href = f"{doc_url_split}{t.attrs['href']}"
                        logger.info('Running POST(download) from temp_url: ' + pdf_nested_href)
                        self.download_pdf_files(pdf_nested_href, file_w_path, doc_url_split)
                else:
                    pass
            except Exception as e:
                logger.warning(f'temp_url download failed: {e}, trying something else..')
                pass

    def pdf_from_content_no_opt(self, r, path_file_name):
        logger.info('Downloading PDF from content response')
        #logger.info(r.text)
        try:
            with open(path_file_name, 'wb') as f:
                f.write(r)
            f.close()
        except Exception as e:
            logger.info('Downloading PDF from content FAILED')
            logger.error(e)
            return 'File Download Failed'

def main():
    pass

# MAIN
if __name__ == '__main__':
    main()
