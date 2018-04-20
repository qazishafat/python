#!/router/bin/python3
# -*- coding: utf-8 -*-

# Logs messages to <product>.log file
# 0 = disabled, 1 = enabled
LOG = 1

# Project Specific variables
# This is the mailer alias where MR emails are received
alias = "cisco.eng.att-ncs5500-idc-mr"

# This is always CSC.swtools. Do not change this unless you know what you are doing :-)
project = "CSC.swtools"

# CDETS Product Field
# Change this to match your CDETS Product
product = "att-idc-ncs5500"

# CDETS Version field. 
# This needs to match your CDETS Version field for your Project / Product
version = "6.3.2"

# CDETS component field under your Project / Product
component = "sukhalid"

# CDETS Attribute field
# Attribute of created CDETS record will be of the format "<MR#> ATT_Rel1"
releaseAttribute = "ATT_IDC_RLS2"

# Default Severity of swtools records created
severity = "6"

import os
import re
import sys
import time
import getpass
import argparse
import logging
import subprocess
from logging import handlers
from nntplib import NNTP
from nntplib import decode_header
from collections import OrderedDict
import math

os.chdir(os.path.dirname(__file__))
cwd = os.getcwd()

# News Server
server = "news.cisco.com"

# this is to used to debug script issues
DEBUG = 0
CONSOLE = 0
counter = 1

# List of components (MR Owners) are saved here 
componentsFile = product + "-Components.txt" 

# temporary workspace where full MR text will be stored
fullTextFile = product + "-MR-Full-Text.txt"

# following are the limits of various fields in CDETS
cdetsHeadlineLimit = 70 	# 72 is the actual limit
cdetsSummaryLimit = 1995 	# 2k is the actual limit
cdetsNotesLimit = 15800		# 16k is the actual limit

# Tempfile. This will be deleted and replaced with each new message
ddtsTemplateFile = product + "-DDTS-Template.txt"

# Logfile (should persist) that contains list of all MRs already in the system      
filedMRsFile = product + "-Filed-MRs.txt" 

projectDict = {}
projectDict["Project"] = project
projectDict["Product"] = product
projectDict["Version"] = version
projectDict["Component"] = component
projectDict["Attribute"] = releaseAttribute
projectDict["Severity"] = severity
projectDict["cdetsSummaryLimit"] = cdetsSummaryLimit
projectDict["cdetsNotesLimit"] = cdetsNotesLimit
projectDict["cdetsHeadlineLimit"] = cdetsHeadlineLimit

# The mrfiler tool involves following steps:
# 1. Connect to Cisco mailer server
# 2. Fetch mr alias message overviews / message count
# 3. Fetch message headers
# 4. Parse email "SUBJECT" to see if its new MR
# 5. If not new MR, go back to step 2
# 6. If new MR, check MR-Filed File to see if this is a known MR
# 7. If known MR, go back to step 2
# 8. If not know, check CDETS if a DDTS exists for this MR
# 9. If exists, and if not in MR-Filed File, update the file
# 10. If no DDTS, parse the message body to extract MR data
# 11. Create DDTS Template
# 12. File DDTS
# 13. Update MR-Filed File

def debugDumpHeader(id, header):
	print("MESSAGE HEADER: Article Id: %s" % id)
	print("=" * 80)
	print(header)
	print("=" * 80)
	print("Subject: %s" % header['subject'])
	print("=" * 80)
	print("From: %s" % header['from'])
	print("=" * 80)

def debugDumpBody (id, body):
	print("MESSAGE BODY: Article Id: %s" % id)
	lines = list(map(lambda line: line.decode(), body.lines))
	print("=" * 80)
	print(lines)
	print("=" * 80)

def setupLogger():
	logFile = product + ".log"	
	loggerName = product
	maxBytes = 2097152
	backupCount = 5

	logger = logging.getLogger(loggerName)
	logger.setLevel(logging.DEBUG)

	file_handler = logging.handlers.RotatingFileHandler(logFile, maxBytes, backupCount)
	file_formatter = logging.Formatter('%(asctime)s: %(name)s: %(levelname)s: %(message)s')
	file_handler.setFormatter(file_formatter)
	logger.addHandler(file_handler)

	if (CONSOLE):
		console_handler = logging.StreamHandler()
		console_handler.setLevel(logging.DEBUG)
		console_formatter = logging.Formatter('%(name)s: %(levelname)s: %(message)s')
		console_handler.setFormatter(console_formatter)
		logger.addHandler(console_handler)

	return logger

def setupMailer(server, alias, product, logger):
	try: 
		mailer = NNTP(server, readermode=True)
		(reply, count, firstMsg, lastMsg, name) = mailer.group(alias)
		if (LOG):
			logger.info('Successfully connected to Server: %s, alias: %s' % (server, alias))
		return (mailer, firstMsg, lastMsg)
	except Exception as e:
		logger.error('Error accessing alias: %s' % alias, exc_info=True)

def processHeader(header, id):
	subject = decode_header(header['subject'])

	if (checkIfNew(subject)):
		MR = extractMRName(subject)
		component = extractComponent(decode_header(header['from']))
		return (MR, component, subject)
	else:
		return False

def processBody(body):
	fullMRText = ""
	mrDict = OrderedDict()
	mrDict = { 
		'MR': '', 
		'Abstract': '',
		'Severity': '',
		'Summary': ''}

	# body is utf-8 encoded object
	# decode each line before processing
	# remove unicode prefix (bom) and quotes from
	# beginning & end of each line

	# body is returned as a namedtyple called ArticleInfo 
	# body =  ArticleInfo(ArticleNum, msgId, lines[])
	# We are interested in body.lines, which contains the body text

	lines = list(map(lambda line: line.decode(), body.lines))

	# We now process each line and extract MR information
	for line in lines:
		# We want to save the entire body text as a string
		# we'll concatenate each line to create the body  string	
		fullMRText += line + "\n"

		# Extract MR Data
		if re.search(':', line):
			if line.startswith("MR:"):
				match = re.split(':',line)
				if (len(match) > 1):
					mrDict['MR'] = match[1].lstrip()
			elif line.startswith("Abstract:"):
				match = re.split(':',line)
				if (len(match) > 1):
					mrDict['Abstract'] = match[1].lstrip()
			elif line.startswith("Severity:"):
				match = re.split(':',line)
				if (len(match) > 1):
					mrDict['Severity'] = match[1].lstrip()
			elif line.startswith("Summary:"):
				match = re.split(':',line)
				if (len(match) > 1):
					mrDict['Summary'] = match[1].lstrip()
	return (mrDict, fullMRText)

def extractComponent(txt):
	# Sample from field
	# 'name@domain.com ("name@domain.com")'
	# we want to extract: name
	if "@" in txt:
		ext = txt.split("@")
		return ext[0]
	else:
		return False

def extractMRName(subject):
	pattern = re.compile('ATTip\\d{5}')
	subject = decode_header(subject)

	match = pattern.match(subject)

	if (match):
		return match.group(0)
	else:
		return False

def checkIfNew(subject):
	# New MR Subject starts with pattern
	# "ATTip<nnnnn>:<Project>:<Status>:<Severity>"
	newMRPattern = re.compile("ATTip\\d{5}:[\\w|\\w\\-]+:New:\\d{1}:")
	subject = decode_header(subject)

	match = newMRPattern.match(subject)

	if (match):
		return subject
	else:
		return False

def getKnownMRList(fh):
	knownMRList = []
	fh.seek(0)
	lines = fh.readlines()
	if lines:
		for line in lines:
			knownMRList.append(line.rstrip())
	return knownMRList

def checkIfDDTSExists(MR, project, product):
	# We'll search swtools project for
	# any ddts with "MR" attribute
	findcr = '/usr/cisco/bin/findcr -c -n -p ' + project + ' \"Product = \'' + product + '\' and Attribute LIKE \'*' + MR + '*\'"'

	ddts = subprocess.check_output(findcr, shell=True, universal_newlines=True)

	if isinstance (ddts, str):
		if (ddts.isdigit()):
			ddts = int(ddts)
		else:
			return False

	if (ddts):
		return True
	else:
		return False

def buildDDTSTemplateFile(projectDict, mrDict, file):
	attribute = ""
	hLimit = projectDict["cdetsHeadlineLimit"]
	sLimit = projectDict["cdetsSummaryLimit"]

	# summaryFooter = "\nPlease see N-comments for additional details."

	abstract = mrDict["Abstract"][:hLimit]
	attribute = mrDict["MR"] + " " + projectDict["Attribute"]
	
	if not (mrDict["Summary"]):
		summary = abstract
	else:
		summary = mrDict["Summary"][:sLimit]
	
	try: 
		with open(file, "w+") as fh:
			fh.write("Project: %s\n" % projectDict["Project"])
			fh.write("Product: %s\n" % projectDict["Product"])
			fh.write("Component: %s\n" % projectDict["Component"])
			fh.write("Version: %s\n" % projectDict["Version"])
			fh.write("Headline: %s\n" % abstract)
			fh.write("Severity: %s\n" % projectDict["Severity"])
			fh.write("Attribute: %s\n" % attribute)
			fh.write("DE-priority: %s\n" % mrDict["Severity"])
			fh.write("Summary: %s" % summary)
			
		return True
	except:
		return False

def buildDDTSFullTextFile(fullText, file, flimit):
	# we'll remove any blank lines from end of the files
	fullText = fullText.rstrip()
	fullText = fullText[:flimit]
	logger.info(" this is fulltext file value %s" % fullText )
	try:
		with open(file, "w+") as fh:
			fh.write(fullText)
		return True
	except:
		return False

def createNewDDTS(templateFile, nCommentsFile):
	
	addcr = "/usr/cisco/bin/addcr -q -T " + templateFile + " -n N-comments -f " + nCommentsFile + " Dev-escape N"

	try:
		ddts = subprocess.check_output(addcr, shell=True, universal_newlines=True)
	except:
		return False

	# addcr returns 0 on success and > 1 on error
	if not ddts:
		return True
	else:
		return False

def main():
	logger = setupLogger()

	# 1. Connect to mailer and retreive first & last MsgIds for mr alias
	try:
		(mailer, firstMsg, lastMsg) = setupMailer(server, alias, product, logger)
	except Exception as e:
		logger.error('Error connecting to alias %s' % alias, exc_info=True)
		quit()

	# 2. Process new MRs - Get message headers for all messages from firstMsg to lastMsg
	try:
		(resp, header ) = mailer.over((firstMsg, lastMsg))
		if (LOG):
			logger.info('Successfully retrieved messages from alias %s' % alias)
	except Exception as e:
		logger.error('Error retrieving messages for alias: %s' % alias, exc_info=True)
		quit()

	mrfh = open(filedMRsFile, "a+")

	knownMRList = []
	knownMRList = getKnownMRList(mrfh)
	# Just making sure we are at the end of the file
	# in case if we have to append New MRs to the list
	mrfh.seek(0,2)

	# 3. Process message header, one message at a time
	# Check if this is a new MR from parsing the header['subject']
	# If new MR, process the message body
	for (id, header) in headers:
		rtn = processHeader(header, id)

		# These are debug functions that will help us debug any issues
		# related to reading the message headers
		if (DEBUG):
			debugDumpHeader(id, header)
			if (id >= counter):
				quit()

		if (rtn):
			# 4. Process message body & extract MR fields - mrDict
			# extract MR summary - we need to write this to MRSummaryFile
			# extract Full MR Text - we need to write this to MRTextFile
			MR = rtn[0]
			comp = rtn[1]
			# 4.1 We are here because message subject shows its a NEW MR
			# Lets check if the MR is in filedMRsFile
			# if its in filedMRsFile, lets check if DDTS exists
			# if DDTS does not exist, lets open a DDTS
			# If DDTS exists, lets move to the next message
			if (MR in knownMRList):
				if (LOG):
					logger.info("%s: MR %s already exists in %s" % (id, MR, filedMRsFile))
				# 4.2 If we are here, the MR is in the filedMRsFile
				# we go to the next message
				continue
			elif checkIfDDTSExists(MR, project, product):
				# 4.3 If we are here, the MR is not in the filedMRsFile
				# But DDTS exists; we need to update the filedMRsFile
				mrfh.write(MR + "\n")
				knownMRList.append(MR)
				if (LOG):
					logger.info("%s: DDTS already exists for %s in Project: %s" % (id, MR, filedMRsFile))
					logger.info("%s: MR %s added to %s" % (id, MR, filedMRsFile))
			else:
			 	# 4.4 If we are here, we have found a new MR in the subject field
			 	# The MR is not in the filedMRsFile & No DDTS exists for this MR
			 	# We need to process the message body and extract MR data
			 	# We need to open a new DDTS and
			 	# We need to then add the MR to the filedMRsFile
				if (LOG): 
					logger.info("%s: No DDTS found for MR %s in Project: %s" % (id, MR, product))
				
				(resp, body) = mailer.body(id)
				# These are debug functions that will help us debug any issues
				# related to reading the message headers
				if (DEBUG):
					debugDumpBody(id, body)
					if (id >= counter):
						quit()

				# Retrieve message body & parse MR data
				(mrDict, fullMRText) = processBody(body)

				if (buildDDTSTemplateFile(projectDict, mrDict, ddtsTemplateFile)):
					if (LOG):
						logger.info("%s: Successfully created DDTS Template File for MR: %s template %s " % (id, MR, mrDict))
					if (buildDDTSFullTextFile(fullMRText, fullTextFile, cdetsNotesLimit)):
						if (LOG):
							logger.info("%s: Successfully created N-comments File for MR: %s" % (id, MR))

						# We'll create the DDTS now
						time.sleep(0.5)
						if (createNewDDTS(ddtsTemplateFile, fullTextFile)):
							# Add the MR to the Filed MR List
							mrfh.write(MR + "\n")
							knownMRList.append(MR)
							if (LOG):
								logger.info("%s: Successfully created swtools record for MR: %s" % (id, MR))
								logger.info("%s: MR %s added to %s" % (id, MR, filedMRsFile))
						else:
							if (LOG):
								logger.error("%s: Error creating swtools record for MR: %s" % (id, MR))
				else:
					logger.error("%s: Error creating swtools record for MR: %s in Project: %s" % (id, MR, product))
		else:
			if (LOG):
				logger.info("%s: Not a new MR, Subject: %s..." % (id, header['subject'][:cdetsHeadlineLimit]))

	time.sleep(1)
	
	try:
		#os.remove(fullTextFile)
		#os.remove(ddtsTemplateFile)
		mrfh.close()
	except:
		pass

	if (LOG):
		logger.info("Successfully processed new messages from alias: %s" % alias)

if __name__ == "__main__":
	main()