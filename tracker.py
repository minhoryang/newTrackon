import sqlite3
import scraper
import socket
from time import time, sleep
from urlparse import urlparse
import urllib
import datetime
from collections import deque
from itertools import islice


incoming_trackers = deque(maxlen=10000)
processing_trackers = False


def enqueue_new_trackers(input_string):
    if len(input_string) <= 40000:
        trackers_list = input_string.split()
        for url in trackers_list:
            try:
                url = validate_url(url)
                print 'URL is ', url
                hostname = urlparse(url).hostname
                print 'hostname is ', hostname
            except RuntimeError:
                continue
            conn = sqlite3.connect('trackon.db')
            c = conn.cursor()
            if c.execute("SELECT host FROM status WHERE host=?", (hostname,)).fetchone():
                print "Tracker already being tracked."
                continue
            present = False
            try:    # If during the iteration the deque is changed by another thread (in this case the process_new_trackers thread), a RuntimeError is thrown
                for tracker_in_deque in incoming_trackers:
                    if urlparse(tracker_in_deque).netloc == urlparse(url).netloc:
                        print "Tracker already in the queue."
                        present = True
                        break
            except RuntimeError:
                pass
            if present is True:
                continue
            all_ips_tracked = get_all_ips_tracked()
            try:
                ip_addresses = get_all_ips(hostname)
            except RuntimeError:
                continue
            exists_ip = set(ip_addresses).intersection(all_ips_tracked)
            if exists_ip:
                print "IP of the tracker already in the list."
                continue
            incoming_trackers.append(url)
            print "Tracker added to the incoming queue"
        print "Finished processing input"
        if processing_trackers is False:
            process_new_trackers()


def process_new_trackers():
    global processing_trackers
    processing_trackers = True
    while incoming_trackers:
        tracker = incoming_trackers.popleft()
        size = len(incoming_trackers)
        print "Size of deque: ", size
        process_new_tracker(tracker)
    print "Deque is empty"
    processing_trackers = False


def get_trackers_status():
    conn = sqlite3.connect('trackon.db')
    conn.row_factory = dict_factory
    c = conn.cursor()
    trackers_list = []
    for row in c.execute("SELECT * FROM STATUS ORDER BY uptime DESC"):
        new_tracker = {'url': row['url'], 'ip': eval(row['ip']), 'latency': row['latency'],
                       'updated': row['last_checked'], 'interval': row['interval'], 'status': row['status'],
                       'uptime': row['uptime'], 'country': eval(row['country']), 'added': row['added'],
                       'network': eval(row['network'])}
        string = ''
        for ip in new_tracker['ip']:
            string += ip + '<br/>'
        new_tracker['ip'] = string

        string = ''
        for country in new_tracker['country']:
            string += country + '<br/>'
        new_tracker['country'] = string

        string = ''
        for network in new_tracker['network']:
            string += network + '<br/>'
        new_tracker['network'] = string

        trackers_list.append(new_tracker)
    return trackers_list


def process_new_tracker(url):
    print '---------------------------------------------------------------'
    print 'New tracker: ' + url

    try:
        url = validate_url(url)
        hostname = urlparse(url).hostname
        ip_addresses = get_all_ips(hostname)
        print 'NEW IPs', ip_addresses
    except RuntimeError, e:
        return url + ": " + str(e)

    all_ips_tracked = get_all_ips_tracked()
    exists_ip = set(ip_addresses).intersection(all_ips_tracked)
    if exists_ip:
        return url + ": IP already being tracked!"

    conn = sqlite3.connect('trackon.db')
    c = conn.cursor()
    exists_name = c.execute("SELECT host FROM status WHERE host=?", (url,)).fetchone()
    print 'Hostname: ' + url
    if exists_name:
        print "Tracker in the list"
        return url + ": Tracker already being tracked!"

    print 'Going to scrape ' + url
    try:
        latency, interval, tracker = scraper.scrape(url)
    except RuntimeError, e:
        return url + ": " + str(e)

    tracker_country, tracker_network = update_ipapi_data(ip_addresses)
    historic = deque(maxlen=1000)
    historic.append(1)
    date = datetime.datetime.now()
    today = "{}-{}-{}".format(date.day, date.month, date.year)

    c.execute("INSERT INTO status VALUES (?, ?, ?, ?, ?, ?, 1, 100, ?, ?, ?, ?)",
              (tracker, hostname, str(ip_addresses), latency, int(time()), interval, str(tracker_country),
               str(historic), today, str(tracker_network)))
    conn.commit()
    conn.close()

    return tracker + ": Tracker up!"


def update_status():
    while True:
        print "UPDATE STATUS LOOP"
        now = int(time())
        conn = sqlite3.connect('trackon.db')
        conn.row_factory = dict_factory
        c = conn.cursor()
        trackers_outdated = c.execute(
            "SELECT url, host, ip, latency, last_checked, status, interval, uptime, "
            "historic, country, network FROM status WHERE (? - last_checked) > interval", (now,)).fetchall()

        trackers_outdated = recheck_trackers(trackers_outdated)

        for t in trackers_outdated:
            c.execute(
                "UPDATE status SET ip=?, latency=?, last_checked=?, status=?, interval=?, uptime=?,"
                " historic=?, country=?, network=? WHERE url=?",
                (str(t['ip']), t['latency'], now, t['status'], t['interval'], t['uptime'],
                 str(t['historic']), str(t['country']), str(t['network']), t['url'])).fetchone()
        conn.commit()
        conn.close()
        print "Finished updating tracker status"
        sleep(30)


def get_150_incoming():
    global incoming_trackers
    string = ''
    if incoming_trackers:
        try:
            for tracker in islice(incoming_trackers, 150):
                string += tracker + '<br>'
            return len(incoming_trackers), string
        except RuntimeError:    # If during the iteration the deque is changed by another thread (in this case the process_new_trackers thread), a RuntimeError is thrown
            pass
    else: return 0, "None"


def recheck_trackers(trackers_outdated):
    for t in trackers_outdated:
        try:
            t['ip'] = get_all_ips(t['host'])
        except RuntimeError:
            pass
        t['country'], t['network'] = update_ipapi_data(t['ip'])
        historic = eval(t['historic'])
        print "TRACKER TO CHECK: " + t['url']
        try:
            t1 = time()
            if urlparse(t['url']).scheme == 'udp':
                t['interval'] = scraper.scrape_udp(t['url'])
            else:
                t['interval'] = scraper.scrape_http(t['url'])
            t['latency'] = time() - t1
            t['status'] = 1
            print "TRACKER UP"

        except RuntimeError:
            t['status'] = 0
            print "TRACKER DOWN"
        historic.append(t['status'])
        t['historic'] = historic
        t['uptime'] = uptime_calculator(historic)

    return trackers_outdated


def ip_api(ip, type):
    try:
        response = urllib.urlopen('http://ip-api.com/line/' + ip + '?fields=' + type)
        tracker_country = response.read()
        sleep(1)    # This wait is to respect the public API of IP-API and not get banned
    except IOError:
        tracker_country = 'Error'
    return tracker_country


def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d


def uptime_calculator(historic):
    uptime = float(0)
    for s in historic:
        uptime += s
    uptime = (uptime / len(historic)) * 100
    return uptime


def update_ipapi_data(ip_list):
    tracker_country = []
    tracker_network = []
    for ip in ip_list:
        tracker_country.append(ip_api(ip, 'country'))
        tracker_network.append(ip_api(ip, 'org'))
    return tracker_country, tracker_network

import re

UCHARS = re.compile('^[a-zA-Z0-9_\-\./:]+$')


def validate_ipv4(s):
    pieces = s.split('.')
    if len(pieces) != 4: return False
    try:
        return all(0 <= int(p) < 256 for p in pieces)
    except ValueError:
        return False


def validate_url(u):
    u = urlparse(u)
    if u.scheme not in ['udp', 'http', 'https']:
        raise RuntimeError("Tracker URLs have to start with 'udp://', 'http://' or 'https://'")

    if UCHARS.match(u.netloc) and UCHARS.match(u.path):
        u = u._replace(path='/announce')
        return "%s" % u.geturl()

    else:
        raise RuntimeError("Invalid announce URL")


def get_ip(hostname):
    try:
        return socket.gethostbyname(hostname)
    except socket.error:
        print "can't get IP"
        raise RuntimeError("Can't get IP of the tracker")


def get_all_ips(hostname):
    try:
        ips = socket.gethostbyname_ex(hostname)[2]
        return ips
    except socket.error:
        raise RuntimeError("Can't get IP of the tracker")


def get_all_ips_tracked():
    conn = sqlite3.connect('trackon.db')
    c = conn.cursor()
    all_ips_of_all_trackers = []
    for tracker_ips in c.execute("SELECT ip FROM status"):
        tracker_ips = eval(tracker_ips[0])
        for ip in tracker_ips:
            all_ips_of_all_trackers.append(ip)
    return all_ips_of_all_trackers
