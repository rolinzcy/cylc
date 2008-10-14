#!/usr/bin/python

"""
|----------------------------------------------------------------------|
|---------| ECOCONNECT CONTROLLER WITH IMPLICIT SEQUENCING |-----------|
|----------------------------------------------------------------------|
                    Hilary Oliver, NIWA, 2008
                   See repository documentation
"""

# PYRO NOTES:
# if using an external pyro nameserver, unregister
# objects from previous runs first:
#try:
#    pyro_daemon.disconnect( task )
#except NamingError:
#    pass

import Pyro.core
import Pyro.naming
# from Pyro.errors import NamingError

import reference_time
from tasks import *
from get_instance import get_instance
import threading

from copy import deepcopy

import logging, logging.handlers

import re
import sys

from dummy_clock import *


class LogFilter(logging.Filter):
    # use in dummy mode to replace log timestamps with dummy clock times

    def __init__(self, dclock, name = "" ):
        logging.Filter.__init__( self, name )
        self.dummy_clock = dclock

    def filter(self, record):
        # replace log message time stamp with dummy time
        record.created = self.dummy_clock.get_epoch()
        return True


class task_manager ( Pyro.core.ObjBase ):

    def __init__( self, start_time, task_list ):
        log.debug("initialising task manager")

        Pyro.core.ObjBase.__init__(self)
    
        self.start_time = start_time
        self.task_list = task_list        # list of task names
        self.task_pool = []               # list of interacting task objects

        self.state_dump_dir = 'STATE'
        if not os.path.exists( self.state_dump_dir ):
            os.makedirs( self.state_dump_dir )

        self.pause_requested = False
        self.shutdown_requested = False

        # dead letter box for use by external tasks
        self.dead_letter_box = dead_letter_box()

        uri = pyro_daemon.connect( self.dead_letter_box, "dead_letter_box" )

        uri = pyro_daemon.connect( dummy_clock, "dummy_clock" )

    def create_task_by_name( self, task_name, ref_time, state = "waiting" ):

        # class creation can increase the reference time so can't check
        # for stop until after creation
        task = get_instance( "tasks", task_name )( ref_time, state )

        if stop_time:
            if int( task.ref_time ) > int( stop_time ):
                task.log.info( task.name + " STOPPING at " + stop_time )
                del task
                return

        task.log.info( "New task created for " + task.ref_time )
        self.task_pool.append( task )
        # connect new task to the pyro daemon

        uri = pyro_daemon.connect( task, task.identity() )

    def create_initial_tasks( self ):

        for task_name in self.task_list:
            state = None
            if re.compile( "^.*:").match( task_name ):
                [task_name, state] = task_name.split(':')

            self.create_task_by_name( task_name, self.start_time, state )


    def remove_dead_soldiers( self ):
        # Remove any tasks in the OLDEST time batch whose prerequisites
        # cannot be satisfied by their co-temporal peers. 

        # This only works for the OLDEST batch; satisfiers can appear
        # later  by abdication in newer batches). 

        # This is useful, e.g., if we start the system at 12Z with
        # topnet turned on, because topnet cannot get input from the
        # 12Z nzlam.

        batches = {}
        for task in self.task_pool:
            if task.ref_time not in batches.keys():
                batches[ task.ref_time ] = [ task ]
            else:
                batches[ task.ref_time ].append( task )

        reftimes = batches.keys()
        reftimes.sort( key = int )
        oldest_rt = reftimes[0]

        dead_soldiers = []
        for task in batches[ oldest_rt ]:
            if not task.will_get_satisfaction( batches[ oldest_rt ] ):
                dead_soldiers.append( task )
    
        for task in dead_soldiers:
            task.log.debug( "abdicating a dead soldier " + task.identity() )
            self.create_task_by_name( task.name, task.next_ref_time() )
            self.task_pool.remove( task )
            pyro_daemon.disconnect( task )

            del task


    def run( self ):

        self.create_initial_tasks()

        while True:
            # MAIN MESSAGE HANDLING LOOP

            # handleRequests() returns after a timeout period has
            # passed, Or at least one request (i.e. remote method call)
            # was handled.  NOTE THAT THIS ONLY APPLIES FOR SINGLE
            # THREADED PYRO. If multithreaded, every remote method call
            # on a single pyro proxy object is handled in its own
            # THREAD; in the main thread handleRequests returns after
            # the a new thread is started, and the actual method calls
            # come in asynchronously: this is no good for us: I want the
            # task pool to interact each time new messages come in, then
            # wait for new messages, and so on, which is synchronous.

            self.process_tasks()
            pyro_daemon.handleRequests( timeout = None )


    def system_halt( self, message ):
        log.critical( 'Halting NOW: ' + message )
        pyro_daemon.shutdown( True ) 
        sys.exit(0)


    def process_tasks( self ):

        if self.shutdown_requested:
            self.system_halt( 'by request' )
 
        if self.pause_requested:
            # no new tasks please
            return
       
        if len( self.task_pool ) == 0:
            self.system_halt('all configured tasks done')

        finished_nzlam_post_6_18_exist = False
        finished_nzlam_post_6_18 = []
        batch_finished = {}
        still_running = []

        for task in self.task_pool:
            # create a new task foo(T+1) if foo(T) just finished
            if task.abdicate():
                task.log.debug( "abdicating " + task.identity() )
                self.create_task_by_name( task.name, task.next_ref_time() )

        # task interaction to satisfy prerequisites
        for task in self.task_pool:

            task.get_satisfaction( self.task_pool )

            task.run_if_ready( self.task_pool, dummy_rate )

            # record some info to determine which task batches 
            # can be deleted (see documentation just below)

            # find any finished nzlam_post tasks
            if task.name == "nzlam_post" and task.state == "finished":
                hour = task.ref_time[8:10]
                if hour == "06" or hour == "18":
                    finished_nzlam_post_6_18_exist = True
                    finished_nzlam_post_6_18.append( task.ref_time )

            # find which ref_time batches are all finished
            # (assume yes, set no if any running task found)
            if task.ref_time not in batch_finished.keys():
                batch_finished[ task.ref_time ] = True

            if not task.is_finished():
                batch_finished[ task.ref_time ] = False

            if task.is_running():
                still_running.append( task.ref_time )

        # DELETE SPENT TASKS i.e. those that are finished AND no longer
        # needed to satisfy the prerequisites of other tasks. Cutoff is
        # therefore any batch older than the
        # most-recent-finished-nzlam_post (still needed by topnet) AND 
        # older than the oldest running task.

        # See repository documentation for a detailed discussion of this.

        if len( still_running ) == 0:
            log.critical( "ALL TASKS DONE" )
            sys.exit(0)

        still_running.sort( key = int )
        oldest_running = still_running[0]

        if finished_nzlam_post_6_18_exist:
            cutoff = oldest_running
            log.debug( "oldest running task: " + cutoff )

            finished_nzlam_post_6_18.sort( key = int, reverse = True )
            most_recent_finished_nzlam_post_6_18 = finished_nzlam_post_6_18[0]

            log.debug( "most recent finished 6 or 18Z nzlam_post: " + most_recent_finished_nzlam_post_6_18 )

            if int( most_recent_finished_nzlam_post_6_18 ) < int( cutoff ): 
                cutoff = most_recent_finished_nzlam_post_6_18

            log.debug( " => keeping tasks " + cutoff + " and newer")
        
            remove_these = []
            for rt in batch_finished.keys():
                if int( rt ) < int( cutoff ):
                    if batch_finished[rt]:
                        log.debug( "REMOVING BATCH " + rt )
                        for task in self.task_pool:
                            if task.ref_time == rt:
                                remove_these.append( task )

            if len( remove_these ) > 0:
                for task in remove_these:
                    log.debug( "removing spent " + task.identity() )
                    self.task_pool.remove( task )
                    pyro_daemon.disconnect( task )

            del remove_these

        self.remove_dead_soldiers()
   
        self.dump_state()


    def request_pause( self ):
        # call remotely via Pyro
        log.warning( "system pause requested" )
        self.pause_requested = True

    def request_resume( self ):
        # call remotely via Pyro
        log.warning( "system resume requested" )
        self.pause_requested = False

    def request_shutdown( self ):
        # call remotely via Pyro
        log.warning( "system shutdown requested" )
        self.shutdown_requested = True

    def get_state_summary( self ):
        summary = {}
        for task in self.task_pool:
            postreqs = task.get_postrequisites()
            n_total = len( postreqs )
            n_satisfied = 0
            for key in postreqs.keys():
                if postreqs[ key ]:
                    n_satisfied += 1

            summary[ task.identity() ] = [ task.state, str( n_satisfied), str(n_total), task.latest_message ]

        return summary

    def dump_state( self ):

        # TO DO: implement restart from dumped state capability 
        # Also, consider:
        #  (i) using 'pickle' to dump and read state, or
        #  (ii) writing a python source file similar to current startup config

        config = {}
        for task in self.task_pool:
            ref_time = task.ref_time
            state = task.name + ":" + task.state
            if ref_time in config.keys():
                config[ ref_time ].append( state )
            else:
                config[ ref_time ] = [ state ]

        FILE = open( self.state_dump_dir + '/state', 'w' )

        ref_times = config.keys()
        ref_times.sort( key = int )
        for rt in ref_times:
            FILE.write( rt + ' ' )
            for entry in config[ rt ]:
                FILE.write( entry + ' ' )

            FILE.write( '\n' )

        FILE.close()

#----------------------------------------------------------------------
class dead_letter_box( Pyro.core.ObjBase ):
    """
    class to take incoming pyro messages that are not directed at a
    specific task object (the sender can direct warning messages here if
    the desired task object no longer exists, for example)
    """

    def __init__( self ):
        log.debug( "Initialising Dead Letter Box" )
        Pyro.core.ObjBase.__init__(self)

    def incoming( self, message ):
        log.warning( "DEAD LETTER: " + message )

#----------------------------------------------------------------------
if __name__ == "__main__":
    # check command line arguments
    n_args = len( sys.argv ) - 1

    def usage():
        print "USAGE:", sys.argv[0], "<config file>"

    print "__________________________________________________________"
    print
    print "      . EcoConnect Implicit Sequencing Controller ."
    print "__________________________________________________________"
    
    # TO DO: better commandline parsing with optparse or getopt
    # (maybe not needed as most input is from the config file?)
    start_time = None
    stop_time = None
    config_file = None

    # dummy mode 
    dummy_mode = False
    dummy_offset = None  
    dummy_rate = 60 
 
    if n_args != 1:
        usage()
        sys.exit(1)

    config_file = sys.argv[1]

    if not os.path.exists( config_file ):
        print
        print "File not found: " + config_file
        usage()
        sys.exit(1)
    
    # load the config file
    print
    print "Using config file " + config_file
    # strip of the '.py'
    m = re.compile( "^(.*)\.py$" ).match( config_file )
    modname = m.groups()[0]
    # load it now
    exec "from " + modname + " import *"

    # check compulsory input
    if not start_time:
        print
        print "ERROR: start_time not defined"
        sys.exit(1)

    if len( task_list ) == 0:
        print
        print "ERROR: no tasks configured"
        sys.exit(1)

    print
    print 'Initial reference time ' + start_time
    if stop_time:
        print 'Final reference time ' + stop_time

    if dummy_mode:
        dummy_clock = dummy_clock( start_time, dummy_rate, dummy_offset ) 


    if not os.path.exists( 'LOGFILES' ):
        os.makedirs( 'LOGFILES' )

    print
    print "Logging to ./LOGFILES"

    log = logging.getLogger( "main" )
    log.setLevel( logging_level )
    max_bytes = 10000
    backups = 5
    h = logging.handlers.RotatingFileHandler( 'LOGFILES/ecocontroller', 'a', max_bytes, backups )
    f = logging.Formatter( '%(asctime)s %(levelname)-8s %(name)-16s - %(message)s', '%Y/%m/%d %H:%M:%S' )
    # use '%(name)-30s' to get the logger name print too 
    h.setFormatter(f)
    log.addHandler(h)

    # write warnings and worse to stderr as well as to the log
    h2 = logging.StreamHandler(sys.stderr)
    h2.setLevel( logging.WARNING )
    h2.setFormatter( f )
    log.addHandler(h2)
    if dummy_mode:
        # replace logged real time with dummy clock time 
        log.addFilter( LogFilter( dummy_clock, "main" ))

    # task-name-specific log files for all tasks 
    # these propagate messages up to the main log
    for name in task_list:
        if re.compile( "^.*:").match( name ):
            [name, state] = name.split( ':' )
        foo = logging.getLogger( "main." + name )
        foo.setLevel( logging_level )

        h = logging.handlers.RotatingFileHandler( 'LOGFILES/' + name, 'a', max_bytes, backups )
        f = logging.Formatter( '%(asctime)s %(levelname)-8s - %(message)s', '%Y/%m/%d %H:%M:%S' )
        h.setFormatter(f)
        foo.addHandler(h)
        if dummy_mode:
            # replace logged real time with dummy clock time 
            foo.addFilter( LogFilter( dummy_clock, "main" ))

    log.info( 'initial reference time ' + start_time )
    log.info( 'final reference time ' + stop_time )

    # single threaded operation is required
    # for the main request loop to work as intended.
    Pyro.config.PYRO_MULTITHREADED = 0

    # START THE PYRO NAMESERVER BY RUNNING 'PYRO-NS' EXTERNALLY 

    # ... it can run in a thread in this program though:
    #print
    #print "Starting pyro nameserver"
    #ns_starter = Pyro.naming.NameServerStarter()
    #ns_thread = threading.Thread( target = ns_starter.start )
    #ns_thread.setDaemon(True)
    #ns_thread.start()
    #ns_starter.waitUntilStarted(10)

    # locate the Pyro nameserver
    pyro_nameserver = Pyro.naming.NameServerLocator().getNS()
    pyro_daemon = Pyro.core.Daemon()
    pyro_daemon.useNameServer(pyro_nameserver)

    # initialise the task manager
    god = task_manager( start_time, task_list )
    # connect to pyro nameserver to allow external control
    uri = pyro_daemon.connect( god, "god" )

    # start processing
    print
    print "Beginning task processing now"
    print
    god.run()
