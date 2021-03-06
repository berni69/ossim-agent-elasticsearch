# -*- coding: utf-8 -*-
#
# Copyright (c) 2017 - 2018 Bernat Mut <berni.emerald@gmail.com>.
#
# This file is part of Alienvault/OSSIM
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

#
# GLOBAL IMPORTS
#

from time import sleep
import sys

#
# LOCAL IMPORTS
#
from Detector import Detector
from Logger import Lazyformat
from ElasticDetector import ElasticDetector, DotAccessibleDict
from Event import Event


class ParserElastic(Detector):
    SKIP_RULE_FIELD = {'query', 'fields', 'data_index'}

    def __init__(self, conf, plugin, conn):
        self._conf = conf  # config.cfg info
        self._plugin = plugin  # plugins/X.cfg info
        self.rules = []  # list of ElasticRules objects
        self.conn = conn
        self.stop_processing = False
        self.sleep_time = 10

        Detector.__init__(self, conf, plugin, conn)
        # Initialize values with config
        self._plugin_config()
        self._parse_rules()
        self.loginfo(Lazyformat("Init ParserElastic"))

    def _fetch(self, document, fields):
        group = []
        for field in fields:
            try:
                group.append(document[field])
            except KeyError:
                group.append("")
                self.logwarn("{} doesn't exists in document {}".format(field, document))
        return group

    def _plugin_config(self):
        self.plugin_id = self._plugin.get("DEFAULT", "plugin_id")
        self.name = self._plugin.get("config", "name")
        self.elastic_url = self._plugin.get("config", "elastic_url")
        self.store_index = self._plugin.get("config", "store_index")
        self.verify_certs = self._plugin.getboolean("config", "verify_certs")
        user = self._plugin.get("config", "elastic_user")
        password = self._plugin.get("config", "elastic_password")
        self.elastic_credentials = None
        if user and password:
            self.elastic_credentials = (user, password)

    def _parse_rules(self):
        try:
            rules = self._plugin.rules()
            for rule_name in rules:
                rule = rules[rule_name]
                el_rule = ElasticRules(rule_name, rule)
                self.rules.append(el_rule)
        except Exception as ex:
            exc_type, exc_obj, exc_tb = sys.exc_info()
            self.logerror(Lazyformat("_parse_rules[{}]:{} {}".format(exc_tb.tb_lineno, exc_type, ex.message)))

    def process(self):
        self.loginfo(Lazyformat("Starting process ParserElastic"))
        try:
            es = ElasticDetector(self.elastic_url, plugin_name=self.name, store_index=self.store_index,
                                 verify_certs=self.verify_certs,credentials=self.elastic_credentials)
        except Exception as ex:
            exc_type, exc_obj, exc_tb = sys.exc_info()
            self.logerror(Lazyformat("process[{}]:{} {}".format(exc_tb.tb_lineno, exc_type, ex)))
            return

        while not self.stop_processing:
            for rule in self.rules:
                try:
                    es.rule_name = rule.name
                    es.plugin_sid = rule.plugin_sid
                    timestamp = es.get_last_timestamp()
                    self.loginfo(Lazyformat("Getting last documents since {}".format(timestamp)))
                    documents = es.get_matches_since(data_index=rule.data_index, timestamp=timestamp, query=rule.query)
                    for doc in documents:
                        wdoc = DotAccessibleDict(doc['_source'])
                        self.loginfo(wdoc)
                        group = self._fetch(wdoc, rule.fields)
                        self.generate(group, rule)
                    es.insert_timestamp()
                except Exception, ex:
                    self.logerror(Lazyformat("Elasticsearch operation to {} failed: {}", self.elastic_url, ex))
                sleep(float(self.sleep_time))

        self.loginfo(Lazyformat("Exiting process()"))

    def stop(self):
        self.logdebug(Lazyformat("Scheduling plugin stop"))
        self.stop_processing = True
        try:
            self.join(1)
        except RuntimeError:
            self.logwarn(Lazyformat("Stopping thread that likely hasn't started"))

    def generate(self, groups, rule):
        self.logwarn(Lazyformat(groups))
        event = Event()
        for key, value in rule.original_rule.iteritems():
            if key not in self.SKIP_RULE_FIELD:
                data = self._plugin.get_replace_array_value(value.encode('utf-8'), groups)
                if data is not None:
                    event[key] = data
        if event is not None:
            self.send_message(event)


class ElasticRules(object):
    def __init__(self, rule_name, rule=None):
        self.name = rule_name
        self.query = rule['query']
        self.fields = rule['fields'].split(',')
        self.plugin_sid = rule['plugin_sid']
        self.data_index = rule['data_index']
        self.original_rule = rule

    def __str__(self):
        return "[{}][{}][{}] -> {}".format(self.name, self.data_index, self.plugin_sid, self.query)
