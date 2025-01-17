#!/usr/bin/env python3

# OpenBACH is a generic testbed able to control/configure multiple
# network/physical entities (under test) and collect data from them. It is
# composed of an Auditorium (HMIs), a Controller, a Collector and multiple
# Agents (one for each network entity that wants to be tested).
#
#
# Copyright © 2016-2020 CNES
#
#
# This file is part of the OpenBACH testbed.
#
#
# OpenBACH is a free software : you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY, without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program. If not, see http://www.gnu.org/licenses/.


"""Provide time series of data generated by OpenBACH jobs"""


__author__ = 'Viveris Technologies'
__credits__ = '''Contributors:
 * David FERNANDES <david.fernandes@viveris.fr>
'''

import os
import sys
import time
import syslog
import os.path
import argparse
import tempfile
import itertools
import traceback
import contextlib

import pandas as pd
import matplotlib.pyplot as plt

import collect_agent
from data_access.post_processing import Statistics, save, _Plot


AGGREGATION_OPTIONS = {'year', 'month', 'day', 'hour', 'minute', 'second'}


@contextlib.contextmanager
def use_configuration(filepath):
    success = collect_agent.register_collect(filepath)
    if not success:
        message = 'ERROR connecting to collect-agent'
        collect_agent.send_log(syslog.LOG_ERR, message)
        sys.exit(message)
    collect_agent.send_log(syslog.LOG_DEBUG, 'Starting job ' + os.environ.get('JOB_NAME', '!'))
    try:
        yield
    except Exception:
        message = traceback.format_exc()
        collect_agent.send_log(syslog.LOG_CRIT, message)
        raise
    except SystemExit as e:
        if e.code != 0:
            collect_agent.send_log(syslog.LOG_CRIT, 'Abrupt program termination: ' + str(e.code))
        raise


def now():
    return int(time.time() * 1000)


def main(
        job_instance_ids, statistics_names, aggregations_periods,
        bins_sizes, offset, maximum, stats_with_suffixes, axis_labels,
        figures_titles, legends_titles, use_legend, add_global, pickle):
    file_ext = 'pickle' if pickle else 'png'
    statistics = Statistics.from_default_collector()
    statistics.origin = 0
    with tempfile.TemporaryDirectory(prefix='openbach-temporal-binning-histogram-') as root:
        for job, fields, aggregations, bin_sizes, labels, titles, legend_titles in itertools.zip_longest(
                job_instance_ids, statistics_names, aggregations_periods,
                bins_sizes, axis_labels, figures_titles, legends_titles,
                fillvalue=[]):
            data_collection = statistics.fetch(
                    job_instances=job,
                    suffix = None if stats_with_suffixes else '',
                    fields=fields)

            # Drop multi-index columns to easily concatenate dataframes from their statistic names
            df = pd.concat([
                plot.dataframe.set_axis(plot.dataframe.columns.get_level_values('statistic'), axis=1, inplace=False)
                for plot in data_collection])
            # Recreate a multi-indexed columns so the plot can function properly
            df.columns = pd.MultiIndex.from_tuples(
                    [('', '', '', '', stat) for stat in df.columns],
                    names=['job', 'scenario', 'agent', 'suffix', 'statistic'])
            plot = _Plot(df)

            if not fields:
                fields = list(df.columns.get_level_values('statistic'))

            metadata = itertools.zip_longest(fields, labels, bin_sizes, aggregations, legend_titles, titles)
            for field, label, bin_size, aggregation, legend, title in metadata:
                if field not in df.columns.get_level_values('statistic'):
                    message = 'job instances {} did not produce the statistic {}'.format(job, field)
                    collect_agent.send_log(syslog.LOG_WARNING, message)
                    print(message)
                    continue

                if label is None:
                    collect_agent.send_log(
                            syslog.LOG_WARNING,
                            'no y-axis label provided for the {} statistic of job '
                            'instances {}: using the empty string instead'.format(field, job))
                    label = ''

                if aggregation is None:
                    collect_agent.send_log(
                            syslog.LOG_WARNING,
                            'invalid aggregation value of {} for the {} '
                            'statistic of job instances {}: choose from {}, using '
                            '"hour" instead'.format(aggregation, field, job, TIME_OPTIONS))
                    aggregation = 'hour'

                if legend is None and use_legend:
                    collect_agent.send_log(
                            syslog.LOG_WARNING,
                            'no legend title provided for the {} statistic of job '
                            'instances {}: using the empty string instead'.format(field, job))
                    legend = ''

                if bin_size is None:
                    collect_agent.send_log(
                            syslog.LOG_WARNING,
                            'no bin size provided for the {} statistic of job '
                            'instances {}: using the default value 100 instead'.format(field, job))
                    bin_size = 100

                figure, axis = plt.subplots()
                axis = plot.plot_temporal_binning_histogram(
                        axis, label, field, None, bin_size,
                        offset, maximum, aggregation, add_global,
                        use_legend, legend)
                if title is not None:
                    axis.set_title(title)
                filepath = os.path.join(root, 'temporal_binning_histogram_{}.{}'.format(field, file_ext))
                save(figure, filepath, pickle, False)
                collect_agent.store_files(now(), figure=filepath)


if __name__ == '__main__':
    with use_configuration('/opt/openbach/agent/jobs/temporal_binning_histogram/temporal_binning_histogram_rstats_filter.conf'):
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument(
                '-j', '--jobs', metavar='ID', nargs='+', action='append',
                required=True, type=int, default=[],
                help='job instances to plot data from')
        parser.add_argument(
                '-s', '--stat', '--statistic', dest='statistics',
                metavar='STATISTIC', nargs='+', action='append', default=[],
                help='statistics names to plot')
        parser.add_argument(
                '-a', '--aggregation', dest='aggregations',
                choices=AGGREGATION_OPTIONS, nargs='+', action='append',
                help='Time criteria for values aggregation')
        parser.add_argument(
                '-b', '--bin-size', dest='bin_sizes', type=int,
                metavar='BIN_SIZE', nargs='+', action='append', default=[],
                help='Size of the bins')
        parser.add_argument(
                '-o', '--offset', type=int, default=0,
                help='Offset of the bins')
        parser.add_argument(
                '-m', '--maximum', type=int, default=None,
                help='Maximum value of the bins')
        parser.add_argument(
                '-w', '--no-suffix', action='store_true',
                help='Do not plot statistics with suffixes')
        parser.add_argument(
                '-y', '--ylabel', dest='ylabel', nargs='+',
                metavar='YLABEL', action='append', default=[],
                help='Label of y-axis')
        parser.add_argument(
                '-t', '--title', dest='title', nargs='+',
                metavar='TITLE', action='append', default=[],
                help='Title of the figure')
        parser.add_argument(
                '-l', '--legend-title', dest='legend_titles', nargs='+',
                metavar='LEGEND_TITLE', action='append', default=[],
                help='Title of the legend')
        parser.add_argument(
                '-g', '--global', '--global-bin', dest='add_global', action='store_true',
                help='Add bin of global measurements')
        parser.add_argument(
                '-p', '--pickle', action='store_true',
                help='Allows to export figures as pickle '
                '(by default figures are exported as image)')
        parser.add_argument(
                '-n', '--hide-legend', '--no-legend', action='store_true',
                help='Do not draw any legend on the graph')

        args = parser.parse_args()
        stats_with_suffixes = not args.no_suffix
        use_legend = not args.hide_legend

        main(
            args.jobs, args.statistics, args.aggregations, args.bin_sizes, args.offset,
            args.maximum, stats_with_suffixes, args.ylabel, args.title, args.legend_titles,
            use_legend, args.add_global, args.pickle)

