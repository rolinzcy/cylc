#!/usr/bin/python

import string
import glob
import sys
import re

import os
#print os.getcwd()

#import pdb

def usage():
    print 'USAGE: ' + sys.argv[0] + '[options] <system definition sub-dir>'
    print 'OPTIONS: -h ... print this USAGE message'
    sys.exit(1)

def indent_more():
    global indent
    global indent_unit
    indent += indent_unit

def indent_less():
    global indent
    global indent_unit
    indent = re.sub( indent_unit, '', indent, 1 )
    
def print_parsed_info():

    global parsed_def

    for k in parsed_def.keys():
        print
        print k + ':' 
        for val in parsed_def[ k ]:
            print ' - ' + val

def generate_req_string( foo ):

    global indent
    global indent_unit

    count = 0
    flunge = {}
    for req in foo:
        count += 1
        timed = False
        if re.search( ':', req ):
            timed = True
            # timed postrequistes
            [ time, req ] = re.split( ':', req, 1 )
            req = re.sub( '^\s+', '', req )
            time = re.sub( '\s+min', '', time )

        # enclose in quotes
        req = '\'' + req + '\''

        # replace '$(TASK_NAME)' with 'self.name'
        req = re.sub( '\$\(TASK_NAME\)', '\' + self.name + \'', req )
    
        # replace '$(REFERENCE_TIME)' or '$(REFERENCE_TIME - XX )'
        m = re.search( '\$\(REFERENCE_TIME\s*-\s*(\d+)\s*\)', req )
        if m:
            req = re.sub( '\$\(REFERENCE_TIME.*\)', '\' + reference_time.decrement( ref_time, ' + m.group(1) + ') + \'', req )
        else:
            req = re.sub( '\$\(REFERENCE_TIME\)', '\' + ref_time + \'', req )

        # strip off any empty strings generated by replacement operations
        req = re.sub( '\'\' \+ ', '', req )
        req = re.sub( ' \+ \'\'', '', req )

        if timed:
            flunge[ float(time) ] = '[' + time + ', ' + req  + ']'
        else:
            flunge[ count ] = req

    count = 0
    strng = ''
    items = flunge.keys()
    items.sort()
    for key in items:
        count += 1
        if count == 1:
            strng = strng + '\n'
        
        strng = strng + indent + indent_unit + flunge[ key ]

        if count < len( foo ):
            strng += ',\n' 

    return strng

def write_requisites( req_type ):
    # req_type should be 'PREREQUISITES' or 'POSTREQUISITES'
    global indent
    global indent_unit
    global parsed_def
    global FILE

    conditional_reqs = {}
    unconditional_reqs = []
    for line in parsed_def[ req_type ]:
        m = re.match( '^([\d,]+)\s*\|\s*(.*)$', line )
        if m:
            [ left, right ] = m.groups()
            if left in conditional_reqs.keys():
                conditional_reqs[ left ].append( right )
            else:
                conditional_reqs[ left ] = [ right ]

        else:
            unconditional_reqs.append( line )
           
    if len( conditional_reqs.keys() ) == 0:
        if req_type == 'PREREQUISITES':
            strng = indent + 'self.prerequisites = requisites( self.name, [' 
        else:
            strng = indent + 'self.postrequisites = timed_requisites( self.name, [' 

        strng += generate_req_string( unconditional_reqs )
        FILE.write( strng + ' ])\n\n' )

    cond_count = 0
    for key in conditional_reqs.keys():
        cond_count += 1
        hours = re.split( ',', key )

        if cond_count == 1:
            str = indent + 'if'
        else:
            str = indent + 'elif'

        for h in hours:
            str += ' int( hour ) == ' + h + ' or'
        str = re.sub( ' or$', ':\n', str )
        FILE.write( str )

        indent_more()

        if req_type == 'PREREQUISITES':
            strng = indent + 'self.prerequisites = requisites( self.name, [' 
        else:
            strng = indent + 'self.postrequisites = timed_requisites( self.name, [' 

        strng += generate_req_string( conditional_reqs[key] + unconditional_reqs )
        FILE.write( strng + ' ])\n\n' )

        indent_less()


#================= MAIN PROGRAM ========================================
def main( argv ):

    global parsed_def
    global FILE

    global indent, indent_unit
    indent = ''
    indent_unit = '    '

    task_class_file = 'task_classes.py'

    if len( argv ) != 2:
        usage()

    if argv[1] == '-h':
        usage()

    system_definition_subdir = argv[1]

    task_def_files = glob.glob( system_definition_subdir + '/def/*' ) 
    task_def_files = task_def_files + glob.glob( system_definition_subdir + '/pydef/*' )

    allowed_keys = [ 'TASK_NAME', 'VALID_REFERENCE_TIMES', 'EXTERNAL_TASK', 'EXPORT',
        'DELAYED_DEATH', 'USER_PREFIX', 'PREREQUISITES', 'POSTREQUISITES' ]

    # open the output file
    FILE = open( task_class_file, 'w' )
    # python interpreter location
    FILE.write( '#!/usr/bin/python\n\n' )
    # auto-generation warning
    # FILE.write( '# THIS FILE WAS AUTO-GENERATED BY ' + argv[0] + '\n' )  
    # preamble
    FILE.write( 
'''
from task_base import task_base, free_task
import job_submit

import reference_time
from requisites import requisites, timed_requisites, fuzzy_requisites
from time import sleep

import os, sys, re
from copy import deepcopy
from time import strftime
import Pyro.core
import logging
\n''')

    n_files = len(task_def_files)
    i = 0

    for task_def_file in task_def_files:

        i = i + 1

        DEF = open( task_def_file, 'r' )
        lines = DEF.readlines()
        DEF.close()

        #header = 'parsing ' + task_def_file  + ' (' + str(i) + '/' + str( n_files ) + ')'
        #ruler = ''
        #for char in header:
        #    ruler = ruler + '_'
    #
    #    print ruler
    #    print header
        print 'parsing ' + task_def_file

        if re.match( '^.*\.pydef$', task_def_file ):
            # this file is a python class definition
            for line in lines:
                FILE.write( line )

            FILE.write( '\n' )
            continue

        current_key = None

        parsed_def = {}
        for lline in lines:

            line = string.strip( lline )

            # skip blank lines
            if re.match( '^\s*$', line ):
                continue

            # skip comment lines
            if re.match( '^\s*#.*', line ):
                continue

            if re.match( '^%.*', line ):
                # new key identified
                current_key = string.lstrip( line, '%' )
                # print 'new key: ' + current_key,
                if current_key not in allowed_keys:
                    print 'ILLEGAL KEY ERROR: ' + current_key
                    sys.exit(1)
                parsed_def[ current_key ] = []

            else:
                if current_key == None:
                    # can this ever happen?
                    print "Error: no key identified"
                    sys.exit(1)
    
                # data associated with current key
                parsed_def[ current_key ].append( line ) 

        # for debugging:
        # print_parsed_info()

        # write the class definition

        # no prerequisites implies derive from free_task parent class
        if len( parsed_def[ 'PREREQUISITES' ] ) == 0:
            parent_class = 'free_task'
        else:
            parent_class = 'task_base'

        task_name = parsed_def[ 'TASK_NAME' ][0]
        # class definition
        FILE.write( 'class ' + task_name + '(' + parent_class + '):\n\n' )

        indent_more()
 
        FILE.write( indent + '# THIS CLASS DEFINITION WAS AUTO-GENERATED FROM ' + task_def_file + '\n' )  
   
        # task name
        FILE.write( indent + 'name = \'' + task_name + '\'\n' )
        # valid hours
        vhrs = parsed_def[ 'VALID_REFERENCE_TIMES' ][0]
        FILE.write( indent + 'valid_hours = [' + vhrs + ']\n' )

        # external task
        FILE.write( indent + 'external_task = \'' + parsed_def[ 'EXTERNAL_TASK' ][0] + '\'\n' )

        # user prefix
        FILE.write( indent + 'user_prefix = \'' + parsed_def[ 'USER_PREFIX' ][0] + '\'\n\n' )

        # quick death? (DEFAULT False)
        quick_death = 'True'
        if 'DELAYED_DEATH' in parsed_def.keys():
            delayed_death = parsed_def[ 'DELAYED_DEATH' ][0]
            if delayed_death == 'True' or delayed_death == 'true' or delayed_death == 'Yes' or delayed_death == 'yes':
                quick_death = 'False'

            FILE.write( indent + 'quick_death = ' + quick_death + '\n\n' )

        if 'EXPORT' in parsed_def.keys():
            strng = indent + 'env_vars = [\n'
            for pair in parsed_def[ 'EXPORT' ]:
                [ var, val ] = re.split( '\s+', pair )
                strng = strng + indent + indent_unit + '[\'' + var + '\', \'' + val + '\'],\n' 

            strng = re.sub( ',\s*$', '', strng )
            strng = strng + ' ]\n\n' 
            FILE.write( strng )

        # class init function
        FILE.write( indent + 'def __init__( self, ref_time, initial_state):\n\n' )

        indent_more()

        FILE.write( indent + '# adjust reference time to next valid for this task\n' )
        FILE.write( indent + 'self.ref_time = self.nearest_ref_time( ref_time )\n' )
        FILE.write( indent + 'ref_time = self.ref_time\n' )
        FILE.write( indent + 'hour = ref_time[8:10]\n\n' )

        # ... prerequisites
        write_requisites( 'PREREQUISITES' )

        # ... postrequisites
        write_requisites( 'POSTREQUISITES' )

        # call parent's init method
        FILE.write( indent + parent_class + '.__init__( self, ref_time, initial_state )\n\n' )

        if 'EXPORT' in parsed_def.keys():
            # override run_external_task() for the export case
            indent_less()
            FILE.write( indent + 'def run_external_task( self ):\n' )
            FILE.write( indent + indent_unit + parent_class + '.run_external_task( self, ' + task_name + '.env_vars )\n\n' )

        indent_less()
        indent_less()
 
    FILE.close()

if __name__ == '__main__':
    main( sys.argv )

