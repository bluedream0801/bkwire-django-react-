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

class BkDataParser:
    def __init__(self):
        pass

    def address_parser(self, addy):
        logger.debug('Address to parse: ' + addy)
        iap = IntAddyParse()
        company_name = None
        company_city = None
        company_state = None
        company_zip = None

        try:
            adr = iap.parse_address(addy)
        except Exception as e:
            logger.warning(f"unable to successfully parse addy: {e}")

        try:
            company_name = adr[0]
        except:
            pass
        try:
            company_city = adr[4]
        except:
            pass
        try:
            company_state = adr[2]
        except:
            pass
        try:
            company_zip = adr[3]
        except:
            pass

        return(company_name, company_city, company_state, company_zip)


    def parse_html_table_to_dic(self, k, soup):
        """Parse HTML Table - Build data object from the results of that HTML table
        We use a screen scrape method to pull data from HTML forms and parse those
        into Objects we store and utilize to download PDF files that we need for
        parsing

        :param k: Case name used as KEY for dictionary
        :param soup: This is HTML form data that we parse for objects we need
        :return: We return the dictionary we build back to MAIN script for logic
        """
        #logger.info(f'Parsing HTML table to Dictionary: {k}')

        a_href_link = {}
        html_table_dic = {}
        html_table_dic[k] = {}
        tax_id_ein = None
        html_table_dic[k]['tax_id_ein'] = None
        html_table_dic[k]['debtor_name'] = None
        html_table_dic[k]['debtor_address'] = None
        #html_table_dic[k]['docket_table'] = None

        html_table_list = []
        my_dke_to_append = []
        docket_table_list = []
        my_test_list_results = []

        #Regex for TAXID
        regex_tax_id  = r"Tax ID \/ EIN: (\d+-\d+)"
        #Regex for Debtor Information
        regex_debtor_info = r"Debtor\d?\d?(?s)(.+[A-Z]{2} [0-9]{5}).+Tax ID"
        regex_debtor_info_nid = r"Debtor\d?\d?(?s)(.+?[A-Z]{2} [0-9]{5})"
        #Regex for Docket Entries
        regex_docket_entries = r"^[0-9]{2}\/[0-9]{2}\/[0-9]{4}(.+)"
        num_table_we_want = r"Docket Text"

        docket_table = None
        table_count = 0
        tables = soup.find_all('table')
        try:
            while(len(tables) > table_count): # Loop through all html tables
                for row in tables[table_count].find_all('tr'): # Find all <tr> tags and loop over them
                    print(row.text)
                    rdi = re.search(regex_debtor_info, row.text)
                    rdi_nid = re.search(regex_debtor_info_nid, row.text)
                    # If we find regex matches from above add to list
                    if rdi:
                        html_table_list = rdi.group(1).split('\n')

                    if rdi_nid:
                        html_table_list = rdi_nid.group(1).split('\n')

                    rti = re.search(regex_tax_id, row.text)
                    if rti:
                        tax_id_ein = rti.group(1)

                    rdt = re.search(regex_docket_entries, row.text)
                    if rdt:
                        docket_table_list.append(str(rdt.group(1)))

                    rdn = re.search(num_table_we_want, row.text)
                    if rdn:
                        docket_table = tables[table_count]

                # Build debtor address from findings with a little join foo
                debtor_address = ' '.join([str(elem) for elem in html_table_list[1:]])
                #logger.debug('debtor_addy: ' + debtor_address)
                # Increment table count
                table_count += 1
            # remove last random string
            if len(docket_table_list) > 0:
                docket_table_list.pop()
            else:
                #logger.debug("docket_table_list=0")
                pass
        except Exception as e:
            #logger.error(f'Parsing HTML tables to Dict FAILED: {e}')
            pass

        if len(html_table_list) > 0:
            debtor_name = html_table_list[0]
        else:
            debtor_name = k

        try: # Lets build a data object with all our things
            html_table_dic[k]['tax_id_ein'] = tax_id_ein
            html_table_dic[k]['debtor_name'] = debtor_name
            html_table_dic[k]['debtor_address'] = debtor_address
            html_table_dic[k]['docket_table'] = docket_table
        except Exception as e:
            #logger.warning(f'Debtor additional info was NULL: {k}: error: {e}')
            pass


        # clear tables for next case in loop
        a_href_link = {}
        docket_table_list = []

        #logger.debug(html_table_dic)
        self.soup_to_html_dic = html_table_dic
        return(html_table_dic)

    def parse_docket_entries(self, html_tables_results, pacer_data_results, pacer_pdf_results):
        html_table_key_list = ['tax_id_ein', 'debtor_name', 'debtor_address', 'docket_table']
        for key,val in html_tables_results.items():
            pacer_pdf_results[key] = {}
            pdf_doc_files = []
            pacer_pdf_results[key]['petition_detected'] = False
            pacer_pdf_results[key]['creditors_detected'] = False
            pacer_data_results[key]['pdf_dl_skip'] = False
            pacer_data_results[key]['pdf_dl_status_201'] = None
            pacer_data_results[key]['pdf_dl_status_204206'] = None
            modified_case_number = pacer_data_results[key]['case_number'].split(':')
            modified_case_link   = pacer_data_results[key]['case_link'].split('/cgi-bin')
            referer_url          = pacer_data_results[key]['case_link'].replace('iqquerymenu.pl','DktRpt.pl')
            pdf_file_name        = pacer_data_results[key]['court_id'] +'.'+ modified_case_number[1]
            pacer_data_results[key]['company_address'] = None
            pacer_data_results[key]['company_city'] = None
            pacer_data_results[key]['company_state'] = None
            pacer_data_results[key]['company_zip'] = None
            pacer_data_results[key]['company_industry'] = None
            pacer_data_results[key]['sub_chapv'] = False

            iap = IntAddyParse()
            if html_tables_results[key]['debtor_address']:
                logger.debug('HTML_TABLE_RESULTS(debtor_address): ' + str(html_tables_results[key]['debtor_address']))
                try:
                    adr = iap.parse_address(html_tables_results[key]['debtor_address'])
                except Exception as e:
                    pacer_data_results[key]['company_address'] = None
                    pacer_data_results[key]['company_city'] = None
                    pacer_data_results[key]['company_state'] = None
                    pacer_data_results[key]['company_zip'] = None
                    pacer_data_results[key]['company_industry'] = None
                    logger.error(e)
                try:
                    pacer_data_results[key]['company_city'] = adr[4]
                except:
                    pacer_data_results[key]['company_city'] = None
                try:
                    pacer_data_results[key]['company_state'] = adr[2]
                except:
                    pacer_data_results[key]['company_state'] = None
                try:
                    pacer_data_results[key]['company_zip'] = adr[3]
                except:
                    pacer_data_results[key]['company_zip'] = None
                try:
                    pacer_data_results[key]['company_industry'] = 'Unknown'
                except:
                    pacer_data_results[key]['company_industry'] = None
                try:
                    pacer_data_results[key]['company_address'] = adr[1] + ' ' + adr[5]
                except:
                    pacer_data_results[key]['company_address'] = html_tables_results[key]['debtor_address']
            else:
                pacer_data_results[key]['company_address'] = None
                pacer_data_results[key]['company_city'] = None
                pacer_data_results[key]['company_state'] = None
                pacer_data_results[key]['company_zip'] = None
                pacer_data_results[key]['company_industry'] = None

            try:
                docket_table_list = copy.copy(pacer_data_results[key]['docket_table'])
                petition_entry = docket_table_list.pop(0)

                # Move this to a function so we can skip it if filed incorrectly(force parse)
                if re.search('Petition for Individuals', petition_entry, re.I):
                    logger.debug(f"Skipping petition for individuals: {key}")
                    del pacer_data_results[key]
                    pass
                elif re.search('Individual Chapter 11 Voluntary Petition', petition_entry):
                    logger.debug(f"Skipping petition for individuals: {key}")
                    del pacer_data_results[key]
                    pass
                elif re.search('Voluntary Petition Individual', petition_entry):#Chapter 11 Voluntary Petition for Individual
                    logger.debug(f"Skipping petition for individuals: {key}")
                    del pacer_data_results[key]
                    pass
                elif re.search('Chapter 11 Voluntary Petition for Individual', petition_entry):
                    logger.debug(f"Skipping petition for individuals: {key}")
                    del pacer_data_results[key]
                    pass                                      
                else:
                    logger.info(f"found petition entry: {petition_entry}")
                    if re.search('Subchapter V', petition_entry, re.I): #Subchapter V
                        logger.debug(f"found subchapter v case: {key}")
                        pacer_data_results[key]['sub_chapv'] = True

                    split_case_link = pacer_data_results[key]['case_link'].split('?')
                    docket_entry_parse_url = petition_entry.split(':bkwsplit:')                    
                    chop_it_up = docket_entry_parse_url[1].split('/doc1/')
                    pdf_file_name_full_wpage = f"{pdf_file_name}.{split_case_link[1]}.{chop_it_up[1]}-petition.pdf"

                    pdf_file_type = 'petition'
                    pacer_pdf_results[key]['petition_detected'] = False
                    pacer_pdf_results[key][pdf_file_name_full_wpage] = False
                    #check_s3_exists = check_against_s3_objects(pdf_file_name_full_wpage, referer_url)

                    #if check_s3_exists == False:
                    #    logger.info('NO S3 object exists in archive')                        
                    #    self.download_pdf_href(docket_entry_parse_url[1], referer_url, pdf_file_name_full_wpage, pacer_data_results, key, pacer_pdf_results)
                        #pdf_doc_files.append({'filename': pdf_file_name_full_wpage, 'link': docket_entry_parse_url[1]})
                        # TODO: New module that just hits the endpoint with a post and returns the dl'd object
                    #else:
                    #    logger.info('S3 exists in archive')
                    #    results = download_pdf_from_s3_archive(pdf_file_name_full_wpage, referer_url)
                    #    if results != False:
                    #        pacer_data_results[key]['pdf_dl_status_201'] = 'complete'
                    #        pacer_pdf_results[key][pdf_file_name_full_wpage] = True
                    #        logger.info('Download from S3 archive SUCCESS')
                    #    else:
                    #        pacer_data_results[key]['pdf_dl_status_201'] = 'incomplete'
                    #        logger.info('Download from S3 archive FAILED')
                    #        logger.error(results)
                    #pdf_doc_files.append({'filename': pdf_file_name_full_wpage, 'link': docket_entry_parse_url[1], 'type': pdf_file_type})
                    #pacer_data_results[key]['pdf_doc_files'] = pdf_doc_files                            
            except Exception as e:
                logger.warning(f"Petition parsing failed: {e}")

            try:
                docket_table_list = copy.copy(pacer_data_results[key]['docket_table'])
                petition_entry = docket_table_list.pop(0)
                count = 0
                for l in docket_table_list:
                    split_case_link = pacer_data_results[key]['case_link'].split('?')
                    if re.search('Unsecured', l):
                        if re.search('Not Timely Filed', l, re.I):
                            pass
                        elif re.search('Extend Time to File', l, re.I):
                            pass
                        else:
                            logger.info(f"found unsecured entry: {l}")
                            docket_entry_parse_url = l.split(':bkwsplit:')
                            chop_it_up = docket_entry_parse_url[1].split('/doc1/')
                            pdf_file_name_full_wpage = f"{pdf_file_name}.{split_case_link[1]}.{chop_it_up[1]}-creditors.pdf"
                            pdf_file_type = 'creditors'
                            pacer_pdf_results[key][pdf_file_name_full_wpage] = False
                            pacer_pdf_results[key]['creditors_detected'] = True                    
                            check_s3_exists = check_against_s3_objects(pdf_file_name_full_wpage, referer_url)
                            count += 1
#                            if check_s3_exists == False:
#                                logger.info('NO S3 object exists in archive')
#                                #this goes back to import, trigger download and perform logic below for results
#                                self.download_pdf_href(docket_entry_parse_url[1], referer_url, pdf_file_name_full_wpage, pacer_data_results, key, pacer_pdf_results)                            
#                            else:
#                                logger.info('S3 exists in archive')
#                                results = download_pdf_from_s3_archive(pdf_file_name_full_wpage, referer_url)
#                                if results != False:
#                                    pacer_data_results[key]['pdf_dl_status_204206'] = 'complete'
#                                    pacer_pdf_results[key][pdf_file_name_full_wpage] = True
#                                    logger.info('Download from S3 archive SUCCESS')
#                                else:
#                                    pacer_data_results[key]['pdf_dl_status_204206'] = 'incomplete'
#                                    logger.info('Download from S3 archive FAILED')
#                                    logger.error(results)
#                            pdf_doc_files.append({'filename': pdf_file_name_full_wpage, 'link': docket_entry_parse_url[1], 'type': pdf_file_type})
#                            pacer_data_results[key]['pdf_doc_files'] = pdf_doc_files                                          
            except Exception as e:
                logger.warning(f"Pacer docket parse fail: {e}")
        return(pacer_data_results, pacer_pdf_results)

    def docket_table_parse(self, docket_table_data, pacer_data_results, k):
        logger.debug("Parsing docket_table entries")
        docket_table_list = []
        docket_table_entries = []
        for t in docket_table_data:
            docket_table_list.append(str(t).strip())
        while("" in docket_table_list) :
            docket_table_list.remove("")

        docket_table_list.pop(0)
        for d in docket_table_list:
            date,link,page,act,docs,attachs = None,None,None,None,None,None
            x = re.findall("<td.*?>(.+?)<\/td>", d)
            try:
                date = x.pop(0)
            except:
                pass

            y = re.findall("\(.+;\s([0-9]{1,3}).+docs\)", d)
            try:
                docs = y[0]
            except:
                pass

            entry = " ".join(x)
            if len(x) > 2:
                act = x.pop()
            else:
                act = entry

            act_strp = self.strip_hyper_links(act)
            data,attaches = self.extract_docket_table_data(entry)
            act = act_strp

            try:
                link = data[0][0]
            except:
                pass
            try:
                page = data[0][1]
            except:
                pass

            docket_details = f"{date}:bkwsplit:{link}:bkwsplit:{page}:bkwsplit:{docs}:bkwsplit:{act}:bkwsplit:{attaches}"
            logger.debug(docket_details)
            docket_table_entries.append(docket_details)
        pacer_data_results[k]['docket_table'] = docket_table_entries

    def extract_docket_table_data(self, entry):        
        l = None
        attachments_var = None
        atts_search_list = []
        l = re.findall("this\.href=\"(.+?)\".*\(([0-9]{1,3})", entry)
        # We need to parse the attachments from the activity feed
        # Could potentially contain Unsecured Creditors as well as files for purchase
        find_loc = entry.find('Attachments')
        # If find(word) found we get all attachments, could be multiple in a single entry
        if find_loc != -1:
            attachments = re.findall("\(Attachments:(.+)", entry)
            # split and append foo to associate attachments with docket entry
            for a in attachments:
                atts_search_list = []
                atts_list = a.split('#') # our delimiter
                for t in atts_list:# get the hyper links
                    atts_search_list.append(re.findall("<a href=\"(.+?)\".+<\/a> (.+)", t.replace('(', '')))
                attachments_var = atts_search_list
        return(l, attachments_var)

    def strip_hyper_links(self, html):
            find_act = None
            scriptOpen, scriptClose = makeHTMLTags("script")
            scriptBody = scriptOpen + scriptOpen.tag_body + scriptClose
            commonHTMLEntity.setParseAction(replaceHTMLEntity)

            # first pass, strip out tags and translate entities
            firstPass = (
                (htmlComment | scriptBody | commonHTMLEntity | anyOpenTag | anyCloseTag)
                .suppress()
                .transformString(str(html))
            )

            # first pass leaves many blank lines, collapse these down
            repeatedNewlines = LineEnd() * (2,)
            repeatedNewlines.setParseAction(replaceWith("\n\n"))
            secondPass = repeatedNewlines.transformString(firstPass)

            # remove strange characters if found
            find_act = re.findall("\\u00a0.+?\) (.+)", secondPass)
            if len(find_act) == 0:
                rdata = secondPass
            else:
                rdata = find_act[0]
            return(rdata)


    def parse_html_data_creditors(self,html):
        # create a regular expression to parse the HTML table
        regex = r'<TABLE.+?>(.*?)<\/TABLE>'
        
        # find all matches of the regular expression
        matches = re.findall(regex, html, flags=re.DOTALL)
        
        # create a list to store the parsed data for each row
        parsed_row_data = []        
        # loop through each table
        for table in matches:
            # create a list of rows
            rows = re.findall(r'<TD.+?><B>(.+?)<\/TD>', table, flags=re.S|re.DOTALL)
            
            # loop through each row
            for row in rows:
                # create a list of columns
                columns = re.findall(r'^(.+?)<\/B>.+?<BR>(.+?)<\/FONT>', row, flags=re.S)
                # add the parsed data from each column to the row list
                if len(columns) > 0:
                    parsed_row_data.append(columns)

        # create creditors dictionary
        creditors_dictionary = []

        for d in parsed_row_data:
            comp_name = d[0][0]
            comp_address = d[0][1].replace('\n', '').replace('<BR>', ' ')
            # add company name and address to list of dicts
            creditors_dictionary.append({'company_name': comp_name, 'company_address': comp_address })

        return creditors_dictionary

def main():
    r1 = BkDataParser()
    #r1.address_parser('Indigo Manor Holdings 3989 Chain Bridge Rd Ste 2 Fairfax, VA 22030 ')
    html = b"""
<html><head><meta http-equiv="Content-Type" content="text/html; charset=UTF-8"><link rel="shortcut icon" href="https://ecf.prb.uscourts.gov/favicon.ico"><title>CM/ECF - U.S. Bankruptcy Court:prb</title>
<script type="text/javascript">document.cookie = "PRTYPE=web; path=/;"</script> <script>var default_base_path = "/"; </script> <link rel="stylesheet" type="text/css" href="./CM_ECF - U.S. Bankruptcy Court_prb_lead_case_files/default.css"><script type="text/javascript" src="./CM_ECF - U.S. Bankruptcy Court_prb_lead_case_files/core.js"></script><script type="text/javascript" src="./CM_ECF - U.S. Bankruptcy Court_prb_lead_case_files/autocomplete.js"></script><script type="text/javascript" src="./CM_ECF - U.S. Bankruptcy Court_prb_lead_case_files/DisableAJTALinks.js"></script><script type="text/javascript">if (top!=self) {top.location.replace(location.href);}</script><script>var default_base_path = "/"; </script><script type="text/javascript" src="./CM_ECF - U.S. Bankruptcy Court_prb_lead_case_files/menu.pl.download"></script></head><body bgcolor="FFFFF0" text="000000" onload="SetFocus()"><iframe id="_yuiResizeMonitor" title="Text Resize Monitor" style="position: absolute; visibility: visible; width: 2em; height: 2em; top: -33px; left: 0px; border-width: 0px;" src="./CM_ECF - U.S. Bankruptcy Court_prb_lead_case_files/saved_resource.html"></iframe>
				<div class="noprint">
				<div id="topmenu" class="yuimenubar yui-module yui-overlay visible" style="z-index: 2; position: static; display: block; visibility: visible;"><div class="bd">
				<img id="cmecfLogo" class="cmecfLogo" src="./CM_ECF - U.S. Bankruptcy Court_prb_lead_case_files/logo-cmecf-sm.png" alt="CM/ECF" title="" onclick="CMECF.MainMenu.showCourtInformation(); return false">
				<ul class="first-of-type">
			
<li class="yuimenubaritem first-of-type" id="yui-gen0" groupindex="0" index="0"><a class="yuimenubaritemlabel" href="https://ecf.prb.uscourts.gov/cgi-bin/iquery.pl"><u>Q</u>uery</a></li>
<li class="yuimenubaritem yuimenubaritem-hassubmenu" id="yui-gen1" groupindex="0" index="1"><a class="yuimenubaritemlabel yuimenubaritemlabel-hassubmenu" href="https://ecf.prb.uscourts.gov/cgi-bin/DisplayMenu.pl?Reports&amp;id=-1"><u>R</u>eports <div class="spritedownarrow"></div></a></li>
<li class="yuimenubaritem yuimenubaritem-hassubmenu" id="yui-gen2" groupindex="0" index="2"><a class="yuimenubaritemlabel yuimenubaritemlabel-hassubmenu" href="https://ecf.prb.uscourts.gov/cgi-bin/DisplayMenu.pl?Utilities&amp;id=-1"><u>U</u>tilities <div class="spritedownarrow"></div></a></li>
<li class="yuimenubaritem" id="yui-gen3" groupindex="0" index="3"><a class="yuimenubaritemlabel" onclick="CMECF.MainMenu.showHelpPage(&#39;&#39;); return false">Help</a></li>
<li class="yuimenubaritem" id="yui-gen4" groupindex="0" index="4"><a class="yuimenubaritemlabel" href="https://ecf.prb.uscourts.gov/cgi-bin/login.pl?logout">Log Out</a></li><li class="yuimenubaritem" id="placeholderForAlertsIcon" groupindex="0" index="5"></li>
				</ul></div>
				<hr class="hrmenuseparator"></div></div>
				
			<script type="text/javascript">
callCreateMenu=function(){
				var fn = "CMECF.MainMenu.createMenu";
				if(typeof CMECF.MainMenu.createMenu == 'function') {
					CMECF.MainMenu.createMenu();
				}
                        }
if (navigator.appVersion.indexOf("MSIE")==-1){window.setTimeout( function(){ callCreateMenu(); }, 1);}else{CMECF.util.Event.addListener(window, "load",  callCreateMenu());}</script> <div id="cmecfMainContent" style="height: 1238px;"><input type="hidden" id="cmecfMainContentScroll" value="40"><script language="JavaScript">
		var IsForm = false;
		var FirstField;
		function SetFocus() {
			if(IsForm) {
				if(FirstField) {
					var ind = FirstField.indexOf('document.',0);
					if(ind == 0)
					{
						eval(FirstField);
					}
					else
					{
						var Code = "document.forms[0]."+FirstField+".focus();";
						eval(Code);
					}
				} else {
					var Cnt = 0;
					while(document.forms[0].elements[Cnt] != null) {
						try {
							if(document.forms[0].elements[Cnt].type != "hidden" &&
									!document.forms[0].elements[Cnt].disabled &&
									!document.forms[0].elements[Cnt].readOnly) {
								document.forms[0].elements[Cnt].focus();
								break;
							}
						}
						catch(e) {}
						Cnt += 1;
					}
				}
			}
			return(true);
		}
		</script>
<script type="text/JavaScript">
document.cookie="PDFHeaderReferer=665902155645889; path=/";
</script>
   <style>
	table.DktRpt
	{
	font-family:times,serif,arial,helvetica,clean,sans-serif;
	font-size: 14px;
	}
   </style>
	<table class="DktRpt" width="100%" align="right" border="0" cellspacing="5"><tbody><tr><td align="right"><h2> <font size="3">CLOSED</font></h2></td></tr></tbody></table><br><br><br><center><b><font size="+1">U.S. Bankruptcy Court<br>District of Puerto Rico (Ponce)<br>Adversary Proceeding #: 22-00033-MAG</font></b></center>
<table width="100%" border="0" cellspacing="10"><tbody><tr>	<td valign="top" width="60%"><font size="+0">
<br><i>Assigned to:</i>      US BANKRUPTCY JUDGE MARIA DE LOS ANGELES GONZALEZ<br><i>Lead BK Case:</i> <a href="https://ecf.prb.uscourts.gov/cgi-bin/DktRpt.pl?356746">
								22-00458</a><br><i>Lead BK Title:</i> RODOLFO RAMIREZ CARRERO and KENDALL ROGGIO VEGA                               <br><i>Lead BK Chapter:</i> 11<div class="noprint"><a href="https://ecf.prb.uscourts.gov/cgi-bin/qryAscCases.pl?357798">Show Associated Cases</a></div><br><i>Demand:</i>           	</font></td>
	<td valign="top" width="40%"><font size="+0">
<br><i>Date Filed:</i>       05/25/22<br><i>Date Terminated:</i>  07/12/22	</font></td>
</tr>
</tbody></table>
<table cellpadding="0" cellspacing="0">
<tbody><tr><td width="12"></td><td valign="top" nowrap=""><i>Nature[s] of Suit:</i> &nbsp; </td><td valign="top" align="left" width="7">21</td><td>&nbsp;</td><td valign="top">Validity, priority or extent of lien or other interest in property</td></tr>
</tbody></table>
<br><font size="5"><b></b>
		</font><table class="DktRpt" width="100%" border="0" cellspacing="1"><tbody><tr></tr><tr>	<td valign="top" width="50%"><font size="+0"><i><b>
<br><b>Plaintiff<br>-----------------------</b></b></i><br><b>RODOLFO RAMIREZ CARRERO</b>
<br>PO BOX 79
<br>SAN GERMAN, PR 00683
<br>SSN / ITIN: xxx-xx-2592
<br>Tax ID / EIN: 66-0549360<br><br>	</font></td>
	<td valign="top" align="right"><font size="+0"><br><br><br>represented by	</font></td>
<td valign="top" width="40%" nowrap=""><font size="+0"><br><br><br><b>WILLIAM M VIDAL</b>
<br>MCS PLAZA
<br>255 PONCE DE LEON AVE SUITE 801
<br>SAN JUAN, PR 00917
<br>787-764-6867 - 399-6415
<br>Fax  : 787-764-6496
<br>Email:&nbsp;<a href="mailto:william.m.vidal@gmail.com">william.m.vidal@gmail.com</a><br><br></font></td></tr><tr>	<td valign="top" width="50%"><font size="+0"><i><b>
<br><b>Plaintiff<br>-----------------------</b></b></i><br><b>KENDALL ROGGIO VEGA</b>
<br>PO BOX 79
<br>SAN GERMAN, PR 00683
<br>SSN / ITIN: xxx-xx-5882<br><br>	</font></td>
	<td valign="top" align="right"><font size="+0"><br><br><br>represented by	</font></td>
<td valign="top" width="40%" nowrap=""><font size="+0"><br><br><br><b>WILLIAM M VIDAL</b>
<br>(See above for address)<br><br></font></td></tr><tr>
	<td valign="top"><font size="+0"><br>V.<br>	</font></td>
</tr>
<tr>	<td valign="top" width="50%"><font size="+0"><i><b>
<br><b>Defendant<br>-----------------------</b></b></i><br><b>OSP Consortium LLC</b>
<br>1519 Ave Poncen de Leon
<br>Suite 1403
<br>San Juan, PR 00908	</font></td>
	<td valign="top" width="20%" align="right"><font size="+0"><br><br><br>represented by	</font></td>
	<td valign="top" width="40%"><font size="+0"><br><br><br><b>TOMAS F. BLANCO-PEREZ</b>
<br>FERRAIUOLI LLC
<br>PO Box 195168
<br>SAN JUAN, PR 00919-5168
<br>787-766-7000
<br>Fax  : 787-766-7001
<br>Email:&nbsp;<a href="mailto:tblanco@ferraiuoli.com">tblanco@ferraiuoli.com</a><br><br><b>FRANCES C BRUNET URIARTE</b>
<br>60 Calle Caribe, Cond. Castillo, Apt. 6B
<br>San Juan, PR 00907
<br>787-612-7792
<br>Email:&nbsp;<a href="mailto:francesbrunet@gmail.com">francesbrunet@gmail.com</a><br><br><b>GUSTAVO A CHICO-BARRIS</b>
<br>FERRAIUOLI LLC
<br>PO BOX 195168
<br>SAN JUAN, PR 00919-5168
<br>787-766-7000
<br>Fax  : 787-766-7001
<br>Email:&nbsp;<a href="mailto:gchico@ferraiuoli.com">gchico@ferraiuoli.com</a><br><i>LEAD ATTORNEY</i><br><br><b>SONIA COLON COLON</b>
<br>FERRAIUOLI, LLC
<br>PO BOX 195168
<br>SAN JUAN, PR 00919-5168
<br>407-982-7310
<br>Fax  : 787-766-7001
<br>Email:&nbsp;<a href="mailto:scolon@ferraiuoli.com">scolon@ferraiuoli.com</a>	</font></td>
</tr></tbody></table><br><table class="DktRpt" border="1" cellpadding="10" cellspacing="0"><tbody><tr><th width="94" nowrap="">Filing Date</th>
<th colspan="2" align="center">#</th><th align="center">Docket Text</th></tr>
<tr><td width="94" nowrap="" valign="bottom">05/25/2022</td><td width="74" valign="top" align="right" style="border-right:0;padding-right:1px;padding-left:2px;white-space:nowrap"><nobr>  &nbsp;</nobr></td><td width="30" valign="top" align="left" style="border-left:0;padding-left:1px"><a href="https://ecf.prb.uscourts.gov/doc1/158029330623" oncontextmenu="this.href=&quot;https://ecf.prb.uscourts.gov/doc1/158029330623&quot;">1</a> <br><nobr>(174&nbsp;pgs; 5&nbsp;docs)</nobr></td><td valign="bottom">  Adversary case 22-00033. 21 (Validity, priority or extent of lien or other interest in property): Complaint by RODOLFO RAMIREZ CARRERO, KENDALL ROGGIO VEGA against OSP Consortium LLC. Fee Amount $350 <i></i> (Attachments: # <a href="https://ecf.prb.uscourts.gov/doc1/158129330624">1</a> Exhibit I - Properties Appraisals # <a href="https://ecf.prb.uscourts.gov/doc1/158129330625">2</a> Exhibit II - POC 8 BPPR # <a href="https://ecf.prb.uscourts.gov/doc1/158129330626">3</a> Exhibit III - POC 9 BPPR # <a href="https://ecf.prb.uscourts.gov/doc1/158129330627">4</a> Exhibit IV - Analysis of Properties Value) (VIDAL, WILLIAM) (Entered: 05/25/2022)</td></tr>
<tr><td width="94" nowrap="" valign="bottom">05/25/2022</td><td width="74" valign="top" align="right" style="border-right:0;padding-right:1px;padding-left:2px;white-space:nowrap"><nobr>  &nbsp;</nobr></td><td width="30" valign="top" align="left" style="border-left:0;padding-left:1px">2</td><td valign="bottom">  Receipt of Complaint(<a href="https://ecf.prb.uscourts.gov/cgi-bin/DktRpt.pl?357798"> 22-00033-MAG</a>) [cmp,cmp] ( 350.00) filing fee. Receipt number C15775100, amount $ 350.00. (RE: related document(s) <a href="https://ecf.prb.uscourts.gov/doc1/158029330623">1</a>) (U.S. Treasury) (Entered: 05/25/2022)</td></tr>
<tr><td width="94" nowrap="" valign="bottom">05/25/2022</td><td width="74" valign="top" align="right" style="border-right:0;padding-right:1px;padding-left:2px;white-space:nowrap"><nobr>  &nbsp;</nobr></td><td width="30" valign="top" align="left" style="border-left:0;padding-left:1px"><a href="https://ecf.prb.uscourts.gov/doc1/158029331471" oncontextmenu="this.href=&quot;https://ecf.prb.uscourts.gov/doc1/158029331471&quot;">3</a> <br><nobr>(1&nbsp;pg)</nobr></td><td valign="bottom">  Notice of appearance and request for notice filed by SONIA COLON COLON on behalf of OSP Consortium LLC. (COLON COLON, SONIA) (Entered: 05/25/2022)</td></tr>
<tr><td width="94" nowrap="" valign="bottom">05/25/2022</td><td width="74" valign="top" align="right" style="border-right:0;padding-right:1px;padding-left:2px;white-space:nowrap"><nobr>  &nbsp;</nobr></td><td width="30" valign="top" align="left" style="border-left:0;padding-left:1px"><a href="https://ecf.prb.uscourts.gov/doc1/158029331477" oncontextmenu="this.href=&quot;https://ecf.prb.uscourts.gov/doc1/158029331477&quot;">4</a> <br><nobr>(1&nbsp;pg)</nobr></td><td valign="bottom">  Notice of appearance and request for notice filed by Gustavo A Chico-Barris on behalf of OSP Consortium LLC. (Chico-Barris, Gustavo) (Entered: 05/25/2022)</td></tr>
<tr><td width="94" nowrap="" valign="bottom">05/25/2022</td><td width="74" valign="top" align="right" style="border-right:0;padding-right:1px;padding-left:2px;white-space:nowrap"><nobr>  &nbsp;</nobr></td><td width="30" valign="top" align="left" style="border-left:0;padding-left:1px"><a href="https://ecf.prb.uscourts.gov/doc1/158029331480" oncontextmenu="this.href=&quot;https://ecf.prb.uscourts.gov/doc1/158029331480&quot;">5</a> <br><nobr>(1&nbsp;pg)</nobr></td><td valign="bottom">  Notice of appearance and request for notice filed by FRANCES C BRUNET URIARTE on behalf of OSP Consortium LLC. (BRUNET URIARTE, FRANCES) (Entered: 05/25/2022)</td></tr>
<tr><td width="94" nowrap="" valign="bottom">05/25/2022</td><td width="74" valign="top" align="right" style="border-right:0;padding-right:1px;padding-left:2px;white-space:nowrap"><nobr>  &nbsp;</nobr></td><td width="30" valign="top" align="left" style="border-left:0;padding-left:1px"><a href="https://ecf.prb.uscourts.gov/doc1/158029331483" oncontextmenu="this.href=&quot;https://ecf.prb.uscourts.gov/doc1/158029331483&quot;">6</a> <br><nobr>(1&nbsp;pg)</nobr></td><td valign="bottom">  Notice of appearance and request for notice filed by Tomas F. Blanco-Perez on behalf of OSP Consortium LLC. (Blanco-Perez, Tomas) (Entered: 05/25/2022)</td></tr>
<tr><td width="94" nowrap="" valign="bottom">05/26/2022</td><td width="74" valign="top" align="right" style="border-right:0;padding-right:1px;padding-left:2px;white-space:nowrap"><nobr>  &nbsp;</nobr></td><td width="30" valign="top" align="left" style="border-left:0;padding-left:1px"><a href="https://ecf.prb.uscourts.gov/doc1/158029332734" oncontextmenu="this.href=&quot;https://ecf.prb.uscourts.gov/doc1/158029332734&quot;">7</a> <br><nobr>(2&nbsp;pgs)</nobr></td><td valign="bottom">  Summons Issued (related documents <a href="https://ecf.prb.uscourts.gov/doc1/158029330623">1</a>). (REYES TORO, WILLIAM) (Entered: 05/26/2022)</td></tr>
<tr><td width="94" nowrap="" valign="bottom">05/26/2022</td><td width="74" valign="top" align="right" style="border-right:0;padding-right:1px;padding-left:2px;white-space:nowrap"><nobr>  &nbsp;</nobr></td><td width="30" valign="top" align="left" style="border-left:0;padding-left:1px"><a href="https://ecf.prb.uscourts.gov/doc1/158029334202" oncontextmenu="this.href=&quot;https://ecf.prb.uscourts.gov/doc1/158029334202&quot;">8</a> <br><nobr>(10&nbsp;pgs; 2&nbsp;docs)</nobr></td><td valign="bottom">  Certificate of service (Attachments: # <a href="https://ecf.prb.uscourts.gov/doc1/158129334203">1</a> Exhibit Summons in an Adversary Proceeding) Filed by WILLIAM M VIDAL on behalf of RODOLFO RAMIREZ CARRERO, KENDALL ROGGIO VEGA. (VIDAL, WILLIAM) (Entered: 05/26/2022)</td></tr>
<tr><td width="94" nowrap="" valign="bottom">06/01/2022</td><td width="74" valign="top" align="right" style="border-right:0;padding-right:1px;padding-left:2px;white-space:nowrap"><nobr>  &nbsp;</nobr></td><td width="30" valign="top" align="left" style="border-left:0;padding-left:1px"><a href="https://ecf.prb.uscourts.gov/doc1/158029345024" oncontextmenu="this.href=&quot;https://ecf.prb.uscourts.gov/doc1/158029345024&quot;">9</a> <br><nobr>(2&nbsp;pgs)</nobr></td><td valign="bottom">  ORDER SETTING INITIAL SCHEDULING CONFERENCE Hearing scheduled September 27, 2022 at 10:00 AM at Microsoft Teams Video &amp; Audio Conferencing and/or Telephonic Hearings. Signed on 6/1/2022.(Colon, Tatiana) (Entered: 06/01/2022)</td></tr>
<tr><td width="94" nowrap="" valign="bottom">06/03/2022</td><td width="74" valign="top" align="right" style="border-right:0;padding-right:1px;padding-left:2px;white-space:nowrap"><nobr>  &nbsp;</nobr></td><td width="30" valign="top" align="left" style="border-left:0;padding-left:1px"><a href="https://ecf.prb.uscourts.gov/doc1/158029352889" oncontextmenu="this.href=&quot;https://ecf.prb.uscourts.gov/doc1/158029352889&quot;">10</a> <br><nobr>(4&nbsp;pgs)</nobr></td><td valign="bottom">  Certificate of service (RE: related document(s)<a href="https://ecf.prb.uscourts.gov/doc1/158029345024">9</a>) Notice Date 06/03/2022. (Admin.) (Entered: 06/04/2022)</td></tr>
<tr><td width="94" nowrap="" valign="bottom">06/17/2022</td><td width="74" valign="top" align="right" style="border-right:0;padding-right:1px;padding-left:2px;white-space:nowrap"><nobr>  &nbsp;</nobr></td><td width="30" valign="top" align="left" style="border-left:0;padding-left:1px"><a href="https://ecf.prb.uscourts.gov/doc1/158029386755" oncontextmenu="this.href=&quot;https://ecf.prb.uscourts.gov/doc1/158029386755&quot;">11</a> <br><nobr>(1&nbsp;pg)</nobr></td><td valign="bottom">  JUDGMENT: Upon the request made by plaintiffs Rodolfo Ramirez Carrero and Kendall Roggio Vega to voluntarily dismiss the instant case at the hearing held on June 17, 2022 (Bankr. No. 22-458, Dkt. 152), it is now ADJUDGED and DECREED that judgment be and is hereby entered dismissing and closing the adversary proceeding. Order due by 7/8/2022. Signed on 6/17/2022.(Diaz, Yolanda) (Entered: 06/17/2022)</td></tr>
<tr><td width="94" nowrap="" valign="bottom">06/19/2022</td><td width="74" valign="top" align="right" style="border-right:0;padding-right:1px;padding-left:2px;white-space:nowrap"><nobr>  &nbsp;</nobr></td><td width="30" valign="top" align="left" style="border-left:0;padding-left:1px"><a href="https://ecf.prb.uscourts.gov/doc1/158029388199" oncontextmenu="this.href=&quot;https://ecf.prb.uscourts.gov/doc1/158029388199&quot;">12</a> <br><nobr>(3&nbsp;pgs)</nobr></td><td valign="bottom">  Certificate of service (RE: related document(s)<a href="https://ecf.prb.uscourts.gov/doc1/158029386755">11</a>) Notice Date 06/19/2022. (Admin.) (Entered: 06/20/2022)</td></tr>
<tr><td width="94" nowrap="" valign="bottom">07/12/2022</td><td width="74" valign="top" align="right" style="border-right:0;padding-right:1px;padding-left:2px;white-space:nowrap"><nobr>  &nbsp;</nobr></td><td width="30" valign="top" align="left" style="border-left:0;padding-left:1px">&nbsp;</td><td valign="bottom">  Adversary Case 2:22-ap-33 Closed . (Colon, Tatiana) (Entered: 07/12/2022)</td></tr>
</tbody></table><br><br><br><br>
			<script type="text/javascript">
				document.cookie = 'RECENT_CASES=' + "356746;";
			</script>
		<hr><center><table border="1" bgcolor="white" width="400"><tbody><tr><th colspan="4"><font size="+1" color="DARKRED">PACER Service Center </font></th></tr><tr><th colspan="4"><font color="DARKBLUE">Transaction Receipt </font></th></tr><tr></tr><tr></tr><tr><td colspan="4" align="CENTER"><font size="-1" color="DARKBLUE">03/21/2023 12:19:31</font></td></tr><tr><th align="LEFT"><font size="-1" color="DARKBLUE"> PACER Login: </font></th><td align="LEFT"><font size="-1" color="DARKBLUE"> Chrisb7712 </font></td><th align="LEFT"><font size="-1" color="DARKBLUE"> Client Code: </font></th><td align="LEFT"><font size="-1" color="DARKBLUE">  </font></td></tr><tr><th align="LEFT"><font size="-1" color="DARKBLUE"> Description: </font></th><td align="LEFT"><font size="-1" color="DARKBLUE"> Docket Report </font></td><th align="LEFT"><font size="-1" color="DARKBLUE"> Search Criteria: </font></th><td align="LEFT"><font size="-1" color="DARKBLUE"> 22-00033-MAG Fil or Ent: filed   Doc From: 0 Doc To: 99999999 Term: included  Headers: included  Format: html Page counts for documents: included </font></td></tr><tr><th align="LEFT"><font size="-1" color="DARKBLUE"> Billable Pages: </font></th><td align="LEFT"><font size="-1" color="DARKBLUE"> 2 </font></td><th align="LEFT"><font size="-1" color="DARKBLUE"> Cost: </font></th><td align="LEFT"><font size="-1" color="DARKBLUE"> 0.20 </font></td></tr><tr></tr><tr></tr></tbody></table></center><br><br></div></body></html>"""
    my_data_set = r1.parse_html_table_to_dic('blakes case', html)

    print(my_data_set)


# MAIN
if __name__ == '__main__':
    main()


"""
NEW FLOW
- Parse Docket Entries for Petition
    - Ensure this is "non-individual"
    - Check for Sub. V declaration
    - Ensure the pdf does not already exist in S3
    - Download the petition and Save if not, else DL from S3 archive
- Parse Associated Case endpoints
    - Attach associated cases to Main case
- Parse Docket Entries for Unsecured wording
    - Ensure this is NOT a CHILD case(we only parse losses from Parent cases)
    - Ensure this pdf does not already exist in S3
    - Download the unsecured creditors and Save if not, else DL from S3 archive
- Parse Creditors endpoint
    - Download the creditors and save
"""