#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Thumbnail Cleaner - GNOME application to remove all invalid thumbnails.
# Copyright 2011 Gautier Portet
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 3 of the License.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307
# USA

NAME = 'Thumbnail Cleaner'
VERSION = '1.0'
print NAME, VERSION

try:
    import gtk
    import gtk.gdk
    import gnome.ui
    import gobject
    gobject.threads_init()
except ImportError:
    gtk = None

from os.path import join, getsize
import os
import sys
from time import time, sleep
from struct import unpack
from urlparse import urlparse

from threading import Thread
import threading

###########################################################################
# Thumbnail Scanner

# states
READY_TO_SCAN, SCANNING, READY_TO_DELETE, DELETING, FINISHED = range(5)
state = READY_TO_SCAN

widgets_states = {
    # READY_TO_SCAN, SCANNING, READY_TO_DELETE, DELETING, FINISHED
    'stop': (0, 1, 0, 1, 0),
}

ORPHAN, INVALID, VALID = range(3)

class ProgressInfo:
    """
    Info on scanning progression.
    """
    def __init__(self):
        self.running = False
        self.progress = -1
        self.current_file = 0
        self.total_files = 0
        self.total_size = 0
        self.deletable_files = 0
        self.deletable_size = 0
        
    def __getitem__(self, key):
        return getattr(self, key)
        
    def __repr__(self):
        return repr(self.__dict__)


class ThumbnailScanner():
    """
    The main thumbnail scanner.
    """
    def __init__(self):
        self.progress = ProgressInfo()
        self.deletable = []

    def scan(self):
        '''Start the walking thread.'''
        self.progress.running = True
        self._do_walk()
        self.progress.running = False

    def get_scan_info(self):
        '''Return a 0 -> 1.0 progress, plus some infos '''
        try:
            if self.progress.current_file > 0:
                self.progress.progress = float(self.progress.current_file) / self.progress.total_files
        except ZeroDivisionError:
            pass
        return self.progress

    def _do_walk(self, *args):
        '''Search for removable thumbnails.'''
        self.deletable = []
        rootdir = os.path.expanduser('~/.thumbnails')

        total = 0
        for root, dirs, files in os.walk(rootdir):
            self.progress.total_files += len(files)

        for root, dirs, files in os.walk(rootdir):
            for name in files:
                filename = join(root, name)
                self.progress.current_file += 1
                status = self._get_status_from_thumbnail(filename, name)
                
                size = getsize(filename)
                self.progress.total_size += size

                if status == ORPHAN:
                    self.deletable.append(filename)
                    self.progress.deletable_files += 1
                    self.progress.deletable_size += size

    def _get_status_from_thumbnail(self, filename, name):
        status = VALID

        uri = self._get_uri_from_thumbnail(filename)
        local_path = None
        p = urlparse(uri)
        if not p[0] or p[0] == 'file':
            local_path = p[2]

        if uri and not local_path:
            # external resource
            pass
        elif local_path and not os.path.lexists(local_path):
            status = ORPHAN
        elif not uri:
            status = INVALID
        return status

    def _get_uri_from_thumbnail( self, filename):
        # fast method to read the URI stored in png metadata
        f = file(filename)
        
        # read first KB
        chunk = f.read(1024);
        f.close()
        
        # find png thumbnail metadata
        THUMBNAIL_STR = "Thumb::URI"
        uri_pos = chunk.find(THUMBNAIL_STR)

        if uri_pos == -1:
                # absent, leave
            return ''
        
        # just before the metadata, there is the size of the metadata
        uri_pos -= 8
        try:
            uri_len = unpack(">L", chunk[uri_pos:uri_pos+4]) [0]
        except:
            return ''

        # now skip metadata name            
        skip = len(THUMBNAIL_STR)+1
        uri_pos += 8 + skip
        uri_len -= skip
        
        # and return the precious uri
        return chunk[uri_pos:uri_pos+uri_len]


class ThreadedThumbnailScanner(Thread, ThumbnailScanner):

    def __init__(self):
        Thread.__init__(self)
        ThumbnailScanner.__init__(self)
        self.daemon = True

    def run(self):
        start = time()
        ThumbnailScanner.scan(self)
        print 'scanned in %.2fs.' % (time() - start)
       
        
def human_size(size):
    units = ((1024*1024*1024, 'GB'), (1024*1024, 'MB'), (1024, 'KB'))
    for i, p in units:
        if size > i:
            return '%.1f %s' % (float(size)/i, p)
    return size


class CLIThumbnailScanner(ThreadedThumbnailScanner):
    def __init__(self):
        ThreadedThumbnailScanner.__init__(self)

    def update_progress(self):
        info = self.get_scan_info()
        if info.total_files == 0:
            message = 'walking...'
        else:
            info.progress *= 100
            info.progress = max(info.progress, 0)
            message = "%(progress).1f%% (%(current_file)d/%(total_files)d)\r" % info
        sys.stdout.write('\r scanning... ' + message)
        sys.stdout.flush()
        return info

    def scan(self):
        self.start()
        while self.isAlive():
            self.update_progress()
            sleep(0.1)
        self.update_progress()
        print
        info = self.get_scan_info()
        
        for f in self.deletable:
            os.remove(f)
        print len(self.deletable), 'outdated thumbnails,',
        print '%s removed.' % human_size(info.deletable_size)


class GTKThumbnailScanner(ThreadedThumbnailScanner):

    def __init__(self):
        ThreadedThumbnailScanner.__init__(self)
        dialog = gtk.Dialog(title=NAME)
        dialog.set_default_size(320,0)
        
        button_close = gtk.Button(stock=gtk.STOCK_CLOSE)
        button_close.connect('clicked', self.on_close)
        dialog.action_area.pack_start(button_close)

        button_clear = gtk.Button(stock=gtk.STOCK_CLEAR)
        button_clear.connect('clicked', self.on_clear)
        button_clear.set_sensitive(False)
        dialog.action_area.pack_start(button_clear)

        vbox = gtk.VBox(spacing=12)
        vbox.set_border_width(12)
        label = gtk.Label()
        label.set_markup(' \n ')
        vbox.pack_start(label)

        progressbar = gtk.ProgressBar()
        progressbar.set_text('')
        vbox.pack_start(progressbar)

        dialog.vbox.set_border_width(12)
        dialog.vbox.pack_start(vbox)
        dialog.show_all()

        dialog.connect('delete_event', self.on_delete_event)
        dialog.connect('destroy', self.on_close)
        self.button_clear = button_clear
        self.label = label
        self.progressbar = progressbar

    def on_delete_event(self, widget, event, data=None):
        return False

    def on_close(self, widget, data=None):
        gtk.main_quit()

    def remove_next_file(self):
        if self.deletable:
            os.remove(self.deletable.pop(0))
            return True
        else:
            self.progressbar.set_text('done')
        
    def on_clear(self, widget, data=None):
        self.button_clear.set_sensitive(False)
        gobject.idle_add(self.remove_next_file)
    
    def update_progress(self):
        info = self.get_scan_info()
        if info.total_files == 0:
            self.progressbar.set_text('walking...')
            self.progressbar.pulse()
        else:
            info.progress = max(info.progress, 0)
            message = "Scanning... (%(current_file)d/%(total_files)d)" % info
            self.progressbar.set_text(message)
            if info.deletable_files:
                self.label.set_markup('<b>%d</b> invalid thumbnails, <b>%s</b>.' % 
                (info.deletable_files, human_size(info.deletable_size)))
            else:
                self.label.set_markup('No outdated thumbnails.')

            self.progressbar.set_fraction(max(info.progress, 0))
        if self.isAlive():
            return True
        else:
            if info.deletable_files:
                self.button_clear.set_sensitive(True)
                self.progressbar.set_text('ready to clear')
            else:
                self.progressbar.set_text('scan finished')

    def scan(self):
        self.start()
        gobject.timeout_add(100, self.update_progress)
        gtk.main()


if __name__ == "__main__":
    if gtk:
        scanner = GTKThumbnailScanner()
    else:
        scanner = CLIThumbnailScanner()
    scanner.scan()

