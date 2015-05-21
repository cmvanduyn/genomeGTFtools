#!/usr/bin/env python

# v1.0 2014-10-22
# v1.1 added option to remove weak blast hits, fixed integer bug 2014-10-28
# v2.0 major revision, for finding gene models with genewise 2015-04-09
# v2.1 added option to output genewise commands to run in parallel 2015-04-13
# v2.2 changes gff output to modern format 2015-05-04
# renamed to blast2genewise.py 2015-05-12
# v2.3 lowered default cutoff, minor edits 2015-05-19
#
# blast2gff.py convert blast output to gff format for genome annotation
# translated from blast2gff.pl and parseblast.pl
#
# and using information from:
# https://www.sanger.ac.uk/resources/software/gff/spec.html
# http://www.sequenceontology.org/gff3.shtml
#
# usage manual for genewise
# http://dendrome.ucdavis.edu/resources/tooldocs/wise2/doc_wise2.html

'''
BLAST2GENEWISE.PY v2.3 2015-05-21

  ## GENERAL OPERATION

blast2genewise.py -b tblastn_output.tab -q refprots.fa -d target_genome.fa

  add -g option to give a specific tag to the gff output, such as
  -g Avic01_ref_genewise

  for each blast hit (probably an exon), direction (+/- strand) is determined
  groups of exons are clumped into a single first/last pair, which is used
  to determine the range on the contig
  this range is then used as an input parameter with the strand for genewise
  currently this is a single process, though may later be integrated with
  GNU parallel, to allow parallel processing of something like 20k commands
  single process operation could take 6-8 hours, which could become 1 hour

  ## GETTING BLAST RESULTS WITH TBLASTN

  tabular blast output should be made from blast programs with -outfmt 6
  such as:

tblastn -query refprots.fa -db target_genome.fa -outfmt 6 > tblastn_output.tab

  without multithread, the blast step could take a long time, maybe 10+ hours
  so it is advisable to use -num_threads 4 or more

  short exons can return many bad hits, so some evalue cutoff is needed
  here 1e-1 is used, but the same can be used for tblastn to reduce file size

  limit number of hits for common domains with -max_target_seqs 10
'''

# TEST
test_prepare = "tblastn -query ml199826a.fasta -db ml2635.fa -outfmt 6 > ml199826a.tblastn6.tab"
test_run = "blast2genewise.py -b ml199826a.tblastn6.tab -q ml199826a.fasta -d ml2635.fa"
test_output = """
ML2635	GeneWise	gene	107522	109142	161.60	-	.	ID=ML199826a.1;Name=ML199826a.1
ML2635	GeneWise	mRNA	107522	109142	.	-	.	ID=ML199826a.1.mrna;Parent=ML199826a.1
ML2635	GeneWise	CDS	108998	109142	.	-	0	ID=ML199826a.1.cds;Parent=ML199826a.1.mrna
ML2635	GeneWise	CDS	108689	108833	.	-	2	ID=ML199826a.1.cds;Parent=ML199826a.1.mrna
ML2635	GeneWise	CDS	107522	107573	.	-	1	ID=ML199826a.1.cds;Parent=ML199826a.1.mrna
ML2635	GeneWise	gene	107039	107517	201.01	-	.	ID=ML199826a.1;Name=ML199826a.1
ML2635	GeneWise	mRNA	107039	107517	.	-	.	ID=ML199826a.1.mrna;Parent=ML199826a.1
ML2635	GeneWise	CDS	107300	107517	.	-	0	ID=ML199826a.1.cds;Parent=ML199826a.1.mrna
ML2635	GeneWise	CDS	107039	107156	.	-	1	ID=ML199826a.1.cds;Parent=ML199826a.1.mrna
ML2635	GeneWise	gene	104608	106411	694.22	-	.	ID=ML199826a.2;Name=ML199826a.2
ML2635	GeneWise	mRNA	104608	106411	.	-	.	ID=ML199826a.2.mrna;Parent=ML199826a.2
ML2635	GeneWise	CDS	106341	106411	.	-	0	ID=ML199826a.2.cds;Parent=ML199826a.2.mrna
ML2635	GeneWise	CDS	105952	106223	.	-	1	ID=ML199826a.2.cds;Parent=ML199826a.2.mrna
ML2635	GeneWise	CDS	105416	105758	.	-	2	ID=ML199826a.2.cds;Parent=ML199826a.2.mrna
ML2635	GeneWise	CDS	105168	105276	.	-	1	ID=ML199826a.2.cds;Parent=ML199826a.2.mrna
ML2635	GeneWise	CDS	104869	105041	.	-	0	ID=ML199826a.2.cds;Parent=ML199826a.2.mrna
ML2635	GeneWise	CDS	104608	104725	.	-	1	ID=ML199826a.2.cds;Parent=ML199826a.2.mrna"""
#
import sys
import os
import argparse
import time
import subprocess
from collections import defaultdict
from Bio import SeqIO
#
### BLAST OUTPUTS
# default blastn or tblastn output for -outfmt 6 is printed tabular of:
#
# qseqid sseqid pident length mismatch gapopen qstart qend sstart send evalue bitscore
#
# referring to: query ID, subject ID, percent identity, length of alignment,
#   mismatch count, gap openings, query start and end, subject start and end,
#   evalue, and the bitscore
#
# for example tblastn output may look:
# prot12345	Contig248	86.89	122	0	1	213	318	16668	17033	1e-63	  214
#
# where protein 12345 hit Contig248 with evalue of 1e-63
#
### GFF FORMAT
# GFF3 format is a tabular of:
#
# seqid source type start end score strand phase attributes
#
# in the context of blast results, this is converted as:
# seqid is the subject ID, so sseqid
# source is BLAST
# type could be mRNA, exon, CDS, but in this case HSP as the BLAST result
# start must always be less or equal to end, so if BLAST hits the reverse 
#   complement, these must be switched
# score is the bitscore
# strand is either + or - for forward and reverse hits
# phase is intron phase used only for "CDS" type, otherwise is "."
# attributes is a list of tag=value; pairs usually including query ID
#
#
# general notes:
#
# to calculate query coverage as on ncbi, appears to be query length / subject length
# to calculate identity % as on ncbi, subject length might have to be absolute value, and 1 must be added as the first base is position 1
# however on ncbi it is calculated as identities / subject length

# class for storing some information about the matches of groups of exons
class ProteinMatch:
	def __init__(self, number):
		# initialize with a unique number
		self.num = number
		self.gstart_position = 0
		self.gend_position = 0
		self.pstart_position = 0
		self.pend_position = 0
		self.covered_length = 0
	def set_start(self, position):
		# start position is the integer start value on the target genome
		self.gstart_position = position
	def set_end(self, position):
		# end position is the integer end value on the target genome
		self.gend_position = position
	def prot_start(self, position):
		# start position is the integer start value on the target genome
		self.pstart_position = position
	def prot_end(self, position):
		# end position is the integer end value on the target genome
		self.pend_position = position
	def set_covlen(self, length):
		self.covered_length = length
	# calculate the coverage as number of amino acids that align at least once divided by query length
	def check_cov(self, querylen, covcutoff):
		self.coverage = float(self.covered_length)/querylen
		if self.coverage >= covcutoff:
			return 1
		else:
			return 0

def write_line(outlist, wayout):
	outline = "\t".join(outlist)
	print >> wayout, outline

def blast_index_to_python(index):
	# python indeces are 1 less than blast
	return index-1

def check_aa_length(pend, pstart, hspLenCutoff):
	if abs(pend-pstart) > hspLenCutoff:
		return True
	else:
		return False

def check_evalue(evalue, evalueCutoff):
	if float(evalue) < evalueCutoff:
		return True
	else:
		return False

def check_bpl(bitscore, protlength, scoreCutoff):
	nbs = float(bitscore)/int(protlength)
	if nbs >= scoreCutoff:
		return True
	else:
		return False

def get_position(hittuples, direction):
	# if direction is 1, get highest value position, which is last in + strand
	# or first position in - strand
	if direction:
		return max([x[1] for x in hittuples])
	# otherwise direction is 0, get lowest value position, so first in + strand
	else:
		return min([x[0] for x in hittuples])

def get_pm_positions(sortedhits, tandemcutoff):
	tandemcounter = 0
	# if no hits are found, perhaps in one direction, then return an empty list
	if not sortedhits:
		return []
	# meaning get first/last positions for each full protein
	# hits should be sorted so that query end positions increase
	lastqend = 50000 # this number must be very big so the first protein is always shorter
	geneEnd = 1000000000
	# empty list to store the ProteinMatch objects
	protMatches = []
	# empty list for the numeric positions that are covered by the query
	protlen = []
	# no previous hit for the start
	prevhit = None
	# generate a protein match each time exon order goes from beginning to end
	for i,hit in enumerate(sortedhits):
		iqend = hit[1]
		# this extends the list by a range of numbers spanning the length of the hit
		# so 5,10 would give [5,6,7,8,9,10]
		protlen.extend(range(hit[0], hit[1]+1) )
		# checks if the current end position is earlier than the max
		if iqend < lastqend:
			# there should be no prevhit for the first hit
			if prevhit:
				# also add the previous value to the match, assuming there is a prevhit
				proteinmatch.set_end(prevhit[3])
				proteinmatch.prot_end(prevhit[1])
				geneEnd = prevhit[3]
				# the protein length that is covered is thus the length of the set, not the sum
				proteinmatch.set_covlen(len(set(protlen)) )
				protMatches.append(proteinmatch)
				# and then the list is reset
				protlen = []
			# if yes, assume that it is the start of a new protein match
			proteinmatch = ProteinMatch(i)
			proteinmatch.set_start(hit[2])
			proteinmatch.prot_start(hit[0])
			# also check here if genes are tandem duplicates
			if hit[2] + tandemcutoff > geneEnd:
				tandemcounter += 1
		elif iqend == lastqend:
			# this probably occurs due to repeated domains and the query being too short
			### TODO figure out how to deal with this
			pass
		lastqend = iqend
		# prevhit is continually storing the previous hit
		# such that if a new hit is found by iqend, the previous value is recorded and added
		prevhit = hit
	else:
		# need to add the final hit
		proteinmatch.set_end(hit[3])
		proteinmatch.prot_end(hit[1])
		proteinmatch.set_covlen(len(set(protlen)) )
		protMatches.append(proteinmatch)
	return protMatches, tandemcounter

def check_match_length(protMatches, querylen, covcutoff, verbose):
	# for each match, check if it is long enough and return the start and end values
	longMatches = []
	# determine if proteins are long enough to search
	for pm in protMatches:
		if pm.check_cov(querylen, covcutoff):
			#flset = (pm.gstart_position, pm.gend_position)
			longMatches.append(pm)
	if verbose:
		print >> sys.stderr, "%d of %d matches are above cutoff" % (len(longMatches), len(protMatches))
	### TODO set length of boundaries by the amount of coverage
	return longMatches

def get_strand(isstart, isend):
	# assume forward is 1 and reverse is 0
	if isstart <= isend:
		return 1
	else:
		return 0

def strand_to_command(strand):
	# assume forward is 1 and reverse is 0
	if strand:
		return "-tfor"
	else:
		return "-trev"

def calc_boundaries(startval, endval, interval, contiglen):
	### TODO determine if d2s is the optimal distance, possibly variable by genome or queries
	# this generates the 5' boundary to search for genes, which is the 5' blast hit minus the defined distance
	uvalue = startval - interval
	# this generates the 3' boundary to search for genes, which is the 3' hit plus the defined distance
	vvalue = endval + interval
	# if that is less than zero, use zero
	if uvalue < 1:
		uvalue = 1
	# if it extends past the length of the contig, use that length instead
	if vvalue > contiglen:
		vvalue = contiglen
	return uvalue, vvalue

#def make_attr_string(query, matchcount, pstart_position, pend_position):
	#targetid = query.replace("|","")
	#attrs = "ID={0}.{1};Target={0} {2} {3}".format(targetid, matchcount, pstart_position, pend_position)
	#return attrs

def print_new_gff(stdoutLines, outFile, queryname, counter, gffTag):
	# output is in bitstring format, so must split by line breaks first
	for line in stdoutLines.split("\n"):
		# disregard empty lines and comment lines
		if line and not line[0] == "/":
			gffSplits = line.split("\t")
			# if tag is present, then use it
			if gffTag:
				gffSplits[1] = str(gffTag)
			# check for correct start/end format, otherwise switch positions
			if int(gffSplits[3]) > int(gffSplits[4]):
				gffSplits[3], gffSplits[4] = gffSplits[4], gffSplits[3]
			# add the modified attribute string
			if gffSplits[2]=="intron": # skip introns
				continue
			elif gffSplits[2]=="match":
				# should be "translated_nucleotide_match" if anything
				gffSplits[2] = "gene"
				attrstring = "ID={0}.{1};Name={0}.{1}".format(queryname, counter)
				gffSplits[8] = attrstring
				print >> outFile, "\t".join(gffSplits)
				parent = "Parent={0}.{1}".format(queryname, counter)
				# mRNA line is necessary, but exons are not
				gffSplits[2] = "mRNA"
				gffSplits[5] = "."
				attrstring = "ID={0}.{1}.mrna;{2}".format(queryname, counter, parent)
				gffSplits[8] = attrstring
				print >> outFile, "\t".join(gffSplits)
				parent = "Parent={0}.{1}.mrna".format(queryname, counter)
			elif gffSplits[2]=="cds":
				gffSplits[2] = "CDS"
				gffSplits[5] = "."
				attrstring = "ID={0}.{1}.cds;{2}".format(queryname, counter, parent)
				gffSplits[8] = attrstring
				print >> outFile, "\t".join(gffSplits)

def main(argv, wayout):
	if not len(argv):
		argv.append("-h")

	parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
	parser.add_argument('-b','--blast', help="blast results file")
	parser.add_argument('-q','--query', help="query file used for the blast")
	parser.add_argument('-d','--db', help="database file use for the blast")
	parser.add_argument('-t','--temp', help="directory for temporary files ['temp/']", default="temp/")
	parser.add_argument('-g','--gff', help="string for gff field instead of Genewise")
	parser.add_argument('-e','--evalue', type=float, default=1e-1, help="evalue cutoff for post blast filtering [1e-1]")
	parser.add_argument('-i','--interval-distance', type=int, default=1000, help="max number of bases from end to hsp to splice site [1000]")
	parser.add_argument('-l','--hsp-length', type=int, default=10, help="minimum hsp length in amino acids [10]")
	parser.add_argument('-m','--min-coverage', type=float, default=0.65, help="minimum query coverage [0.65]")
	parser.add_argument('-n','--tandem-distance', type=int, default=10000, help="allowed distance between full hits [10000]")
	parser.add_argument('-s','--score-cutoff', type=float, help="bitscore/length cutoff for filtering [0.2]", default=0.2)
	parser.add_argument('-C','--commands', action="store_true", help="write commands to file instead of running")
	parser.add_argument('-p','--processors', type=int, default=1, help="number of processors for parallel [1]")
	parser.add_argument('-v','--verbose', help="verbose output", action="store_true")
	args = parser.parse_args(argv)

	# counter for number of lines, and inferred exons
	linecounter = 0
	exoncounter = 0
	# counter for number of hits that are filtered
	badhits = 0

	# define temporary directory
	tempdir = os.path.abspath(args.temp)
	if os.path.isdir(tempdir):
		print >> sys.stderr, "The directory {0} already exists, and will be used".format(tempdir)
	else:
		print >> sys.stderr, "Making the directory {0}".format(tempdir)
		os.mkdir(tempdir)

	starttime = time.time()
	print >> sys.stderr, "Starting BLAST parsing on %s" % (args.blast), time.asctime()
	print >> sys.stderr, "Bad hits will be removed with the following parameters"
	print >> sys.stderr, "Min length: %d; Min e-value: %.2e; Min bits-to-length ratio: %.2f" % (args.hsp_length, args.evalue, args.score_cutoff)

	# PART 1
	#
	# make this a dictionary of dicts, so that query-contig pairs can be indexed quickly
	blasthitdict = defaultdict(dict)
	for line in open(args.blast, 'r'):
		linecounter += 1
		qseqid, sseqid, pident, length, mismatch, gapopen, qstart, qend, sstart, send, evalue, bitscore = line.rstrip().split("\t")
		# convert strings of start and end to integers for calculations
		isend = int(send)
		isstart = int(sstart)
		iqend = int(qend)
		iqstart = int(qstart)

		# first check for hsps being to short
		if not check_aa_length(iqend, iqstart, args.hsp_length):
			badhits += 1
			continue
		# second check for evalue being too poor
		if not check_evalue(evalue, args.evalue):
			badhits += 1
			continue
		# third check for bits per length
		if not check_bpl(bitscore, length, args.score_cutoff):
			badhits += 1
			continue

		# is checks are passed, accumulate that hit an info into dict
		hittuple = (iqstart, iqend, isstart, isend)
		# must use setdefault since get(qseqid, []) returns None, and cannot append
		blasthitdict[sseqid].setdefault(qseqid, []).append(hittuple)
		exoncounter += 1
	print >> sys.stderr, "Parsed %d lines" % (linecounter), time.asctime()
	print >> sys.stderr, "Counted %d putative exons" % (exoncounter), time.asctime()
	print >> sys.stderr, "Removed %d weak hits" % (badhits), time.asctime()

	# PART II
	#
	# counter for strandedness of the hits
	plusexons, minusexons = 0,0	
	plusstrand, minusstrand = 0,0
	# counter for potentially overlapping hits or tandem duplicates
	tandemdups = 0
	hitDictCounter = defaultdict(int)

	print >> sys.stderr, "Reading query sequences from %s" % (args.query), time.asctime()
	querydict = SeqIO.to_dict(SeqIO.parse(args.query, 'fasta'))
	print >> sys.stderr, "Counted %d query sequences" % (len(querydict)), time.asctime()
	print >> sys.stderr, "Reading database sequences from %s" % (args.db), time.asctime()
	dbdict = SeqIO.to_dict(SeqIO.parse(args.db, 'fasta'))
	print >> sys.stderr, "Counted %d db sequences" % (len(dbdict)), time.asctime()

	# for either direct output or written as output for each command
	#genewiseGff = "{}_genewise_{}.gff".format(args.blast, time.strftime("%Y%m%d") )
	genewiseGff = "{}_genewise_{}.gff".format(args.blast, time.strftime("%H%M%S") )
	if os.path.isfile(genewiseGff):
		print >> sys.stderr, "File %s already exists, results will be appended" % (genewiseGff), time.asctime()
	# if using commands argument, make a file listing the commands instead of running them
	if args.commands:
		# generate a file for commands to be called in parallel
		genewiseCommands = "genewise_commands_{}.sh".format(time.strftime("%H%M%S"))
		genewiseoutput = genewiseCommands
		print >> sys.stderr, "Writing commands to %s" % (genewiseCommands), time.asctime()
	else:
		# generate the gff output filename
		genewiseoutput = genewiseGff
		print >> sys.stderr, "Writing hits to %s" % (genewiseGff), time.asctime()

	# in either case for the output, write to genewiseoutput file
	with open(genewiseoutput, 'a') as gwo:
		# sort through each contig
		print >> sys.stderr, "Sorting %d contigs with valid hits" % (len(blasthitdict) ), time.asctime()
		for contig in blasthitdict.iterkeys():
			# make temporary file for each contig with hits
			contigName = os.path.join(tempdir, "{}.fa".format(contig) ).replace("|","_")
			with open(contigName, 'w') as cf:
				cf.write(dbdict[contig].format("fasta") )

			contiglen = len(dbdict[contig].seq)

			# make a list of each query that hit the contig, which is the keys
			matchingqueries = blasthitdict[contig].keys()

			for query in matchingqueries:
				# if that query is used, make a temp file of only that sequence
				queryName = os.path.join(tempdir, "{}.fa".format(query) ).replace("|","_")
				with open(queryName, 'w') as qf:
					qf.write(querydict[query].format("fasta") )

			# separate hits by strand, so only those on the same strand are counted together
				forwardhits = []
				reversehits = []
				for hit in blasthitdict[contig][query]:
					# strand is defined by whether the end is larger than the start value
					strand = get_strand(hit[2], hit[3])
					# depending on strand, add to forward or reverse
					if strand:
						forwardhits.append(hit)
						plusexons += 1
					else:
						reversehits.append(hit)
						minusexons += 1
				# to check if full hits are long enough, get the length of the query protein
				querylen = len(querydict[query].seq)
				targetid = query.replace("|","")

			# within forward hits
				if forwardhits:
					protMatches, tandems = get_pm_positions(sorted(forwardhits, key=lambda x: x[3]), args.tandem_distance)
					longMatches = check_match_length(protMatches, querylen, args.min_coverage, args.verbose)
					tandemdups += tandems
					for pm in longMatches:
						plusstrand += 1
						hitDictCounter[targetid] += 1
						uvalue, vvalue = calc_boundaries(pm.gstart_position, pm.gend_position, args.interval_distance, contiglen)
						# strand should be either -trev or -tfor
						# which are the genewise options for reverse (-) and forward (+)
						gwCommand = ["genewise", "-gff", "-tfor", "-u", "%d" % uvalue, "-v", "%d" % vvalue, queryName, contigName]
						if args.commands:
							print >> gwo, " ".join(gwCommand), ">> {}".format(genewiseGff)
						else:
							if args.verbose:
								print >> sys.stderr, "Calling command:\n%s" % (" ".join(gwCommand) )
							gwcall = subprocess.Popen(gwCommand, stdout=subprocess.PIPE)
							gffLines = gwcall.communicate()[0]
							# if there is any output
							if gffLines:
								#IDstring = make_attr_string(targetid, hitDictCounter[targetid], pm.pstart_position, pm.pend_position)
								print_new_gff(gffLines, gwo, targetid, hitDictCounter[targetid], args.gff)

				# within reverse hits
				if reversehits:
					protMatches, tandems = get_pm_positions(sorted(reversehits, key=lambda x: x[2], reverse=True), args.tandem_distance)
					longMatches = check_match_length(protMatches, querylen, args.min_coverage, args.verbose)
					tandemdups += tandems
					for pm in longMatches:
						minusstrand += 1
						hitDictCounter[targetid] += 1
						# gend and gstart positions are switched, since it is reverse
						uvalue, vvalue = calc_boundaries(pm.gend_position, pm.gstart_position, args.interval_distance, contiglen)
						gwCommand = ["genewise", "-gff", "-trev", "-u", "%d" % uvalue, "-v", "%d" % vvalue, queryName, contigName]
						if args.commands:
							print >> gwo, " ".join(gwCommand), ">> {}".format(genewiseGff)
						else:
							if args.verbose:
								print >> sys.stderr, "Calling command:\n%s" % (" ".join(gwCommand) )
							gwcall = subprocess.Popen(gwCommand, stdout=subprocess.PIPE)
							gffLines = gwcall.communicate()[0]
							# if there is any output
							if gffLines:
								print_new_gff(gffLines, gwo, targetid, hitDictCounter[targetid], args.gff)

	# these counts relate only to the preprocessing, and not to the output of genewise
	print >> sys.stderr, "Counted %d forward exons and %d reverse exons" % (plusexons, minusexons), time.asctime()
	print >> sys.stderr, "Found %d forward and %d reverse hits" % (plusstrand, minusstrand), time.asctime()
	print >> sys.stderr, "Found %d possible tandem duplicates" % (tandemdups), time.asctime()
	print >> sys.stderr, "Processed completed in %.1f minutes" % ( (time.time()-starttime)/60)
	if args.commands:
		genewiselog = "{}_genewise.log".format(args.blast)
		print >> sys.stderr, "RUN THIS COMMAND TO START PARALLEL PROCESSING:"
		print >> sys.stderr, "parallel --gnu -a {0} -j {1} --joblog {2} --halt 1".format(genewiseCommands, args.processors, genewiselog)

if __name__ == "__main__":
	main(sys.argv[1:],sys.stdout)
