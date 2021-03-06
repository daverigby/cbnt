"""
Utilities for helping with the analysis of data, for reporting purposes.
"""

import logging

from lnt.util import stats
from lnt.server.ui import util
from lnt.testing import FAIL

LOGGER_NAME = "lnt.server.ui.app"
logger = logging.getLogger(LOGGER_NAME)

REGRESSED = 'REGRESSED'
IMPROVED = 'IMPROVED'
UNCHANGED_PASS = 'UNCHANGED_PASS'
UNCHANGED_FAIL = 'UNCHANGED_FAIL'
UNSTABLE_REGRESSED = 'UNSTABLE_REGRESSED'
UNSTABLE_IMPROVED = 'UNSTABLE_IMPROVED'

# The smallest measureable change we can detect.
MIN_VALUE_PRECISION = 0.001
MIN_REGRESSION_PCT = 0.01

def absmin_diff(current, prevs):
    """Min of differences between current sample and all previous samples.
    Given more than one min, use the last one detected which is probably a
    newer value. Returns (difference, prev used)
    """
    try:
        diffs = [abs(current-prev) for prev in prevs]
    except:
        print current, prevs
        import sys
        sys.exit(1)
    smallest_pos = 0
    smallest = diffs[0]
    for i, diff in enumerate(diffs):
        if diff <= smallest:
            smallest = diff
            smallest_pos = i
    return current-prevs[smallest_pos], prevs[smallest_pos]


def calc_geomean(run_values):
    # NOTE Geometric mean applied only to positive values, so fix it by
    # adding MIN_VALUE to each value and substract it from the result.
    # Since we are only interested in the change of the central tendency,
    # this workaround is good enough.

    values = [v + MIN_VALUE_PRECISION for v in run_values]

    if not values:
        return None

    return util.geometric_mean(values) - MIN_VALUE_PRECISION


class ComparisonResult:
    """A ComparisonResult is ultimatly responsible for determining if a test
    improves, regresses or does not change, given some new and old data."""

    def __init__(self, aggregation_fn,
                 cur_failed, prev_failed, samples, prev_samples,
                 cur_hash, prev_hash, cur_profile=None, prev_profile=None,
                 confidence_lv=0.05, bigger_is_better=False, stable_test=True):
        self.aggregation_fn = aggregation_fn

        # Special case: if we're using the minimum to aggregate, swap it for max
        # if bigger_is_better.
        if aggregation_fn == stats.safe_min and bigger_is_better:
            aggregation_fn = stats.safe_max

        self.cur_hash = cur_hash
        self.prev_hash = prev_hash
        self.cur_profile = cur_profile
        self.prev_profile = prev_profile

        if samples:
            self.current = aggregation_fn(samples)
        else:
            self.current = None

        self.previous = None
        # Compute the comparison status for the test value.
        self.delta = 0
        self.pct_delta = 0.0
        if self.current and prev_samples:
            prev_median = stats.median(prev_samples)
            self.delta, value = absmin_diff(self.current, [prev_median])
            if value != 0:
                self.pct_delta = self.delta / value
            self.previous = value

        # If we have multiple values for this run, use that to estimate the
        # distribution.
        #
        # We can get integer sample types here - for example if the field is
        # .exec.status. Make sure we don't assert by avoiding the stats
        # functions in this case.
        if samples and len(samples) > 1 and isinstance(samples[0], float):
            self.stddev = stats.standard_deviation(samples)
            self.MAD = stats.median_absolute_deviation(samples)
        else:
            self.stddev = None
            self.MAD = None

        if prev_samples and len(prev_samples) > 1 and isinstance(prev_samples[0], float):
            self.prev_stddev = stats.standard_deviation(prev_samples)
        else:
            self.prev_stddev = None

        self.stddev_mean = None  # Only calculate this if needed.
        self.failed = cur_failed
        self.prev_failed = prev_failed
        self.samples = samples
        self.prev_samples = prev_samples

        self.confidence_lv = confidence_lv
        self.bigger_is_better = bigger_is_better
        self.stable_test = stable_test

    @property
    def stddev_mean(self):
        """The mean around stddev for current sampples. Cached after first call.
        """
        if not self.stddev_mean:
            self.stddev_mean = stats.mean(self.samples)
        return self.stddev_mean

    def __repr__(self):
        """Print this ComparisonResult's constructor.

        Handy for generating test cases for comparisons doing odd things."""
        fmt = "{}(" + "{}, " * 9 + ")"
        return fmt.format(self.__class__.__name__,
                          self.aggregation_fn.__name__,
                          self.failed,
                          self.prev_failed,
                          self.cur_hash,
                          self.prev_hash,
                          self.samples,
                          self.prev_samples,
                          self.confidence_lv,
                          bool(self.bigger_is_better))
                          
    def __json__(self):
        simple_dict = self.__dict__
        simple_dict['aggregation_fn'] = self.aggregation_fn.__name__
        return simple_dict

    def is_result_performance_change(self):
        """Check if we think there was a performance change."""
        if self.get_value_status() in (REGRESSED, IMPROVED, UNSTABLE_IMPROVED,
                                       UNSTABLE_REGRESSED):
            return True
        return False

    def is_result_interesting(self):
        """is_result_interesting() -> bool

        Check whether the result is worth displaying, either because of a
        failure, a test status change or a performance change."""
        if self.get_test_status() != UNCHANGED_PASS:
            return True
        if self.get_value_status() in (REGRESSED, IMPROVED, UNSTABLE_IMPROVED,
                                       UNSTABLE_REGRESSED):
            return True
        return False

    def get_test_status(self):
        # Compute the comparison status for the test success.
        if self.failed:
            if self.prev_failed:
                return UNCHANGED_FAIL
            else:
                return REGRESSED
        else:
            if self.prev_failed:
                return IMPROVED
            else:
                return UNCHANGED_PASS

    # FIXME: take into account hash of binary - if available. If the hash is
    # the same, the binary is the same and therefore the difference cannot be
    # significant - for execution time. It can be significant for compile time.
    def get_value_status(self, confidence_interval=2.576,
                         value_precision=MIN_VALUE_PRECISION,
                         ignore_small=True):
        if self.current is None or self.previous is None:
            return None

        # Don't report value errors for tests which fail, or which just started
        # passing.
        #
        # FIXME: One bug here is that we risk losing performance data on tests
        # which flop to failure then back. What would be nice to do here is to
        # find the last value in a passing run, or to move to using proper keyed
        # reference runs.
        if self.failed:
            return UNCHANGED_FAIL
        elif self.prev_failed:
            return UNCHANGED_PASS 

        # Always ignore percentage changes below 1%, for now, we just don't have
        # enough time to investigate that level of stuff.
        if ignore_small and abs(self.pct_delta) < MIN_REGRESSION_PCT:
            return UNCHANGED_PASS

        # Always ignore changes with small deltas. There is no mathematical
        # basis for this, it should be obviated by appropriate statistical
        # checks, but practical evidence indicates what we currently have isn't
        # good enough (for reasons I do not yet understand).
        if ignore_small and abs(self.delta) < .001:
            return UNCHANGED_PASS

        # Ignore tests whose delta is too small relative to the precision we can
        # sample at; otherwise quantization means that we can't measure the
        # standard deviation with enough accuracy.
        if abs(self.delta) <= 2 * value_precision * confidence_interval:
            return UNCHANGED_PASS

        # If we have a comparison window, then measure using a symmetic
        # confidence interval.
        if self.stddev is not None:
            is_significant = abs(self.delta) > (self.stddev *
                                                confidence_interval)

            # If the delta is significant, return
            if is_significant:
                if self.delta < 0:
                    if self.stable_test:
                        return REGRESSED if self.bigger_is_better else IMPROVED
                    else:
                        return UNSTABLE_REGRESSED if self.bigger_is_better else UNSTABLE_IMPROVED
                else:
                    if self.stable_test:
                        return IMPROVED if self.bigger_is_better else REGRESSED
                    else:
                        return UNSTABLE_IMPROVED if self.bigger_is_better else UNSTABLE_REGRESSED
            else:
                return UNCHANGED_PASS

        if self.prev_stddev is not None:
            is_significant = abs(self.delta) > (self.prev_stddev *
                                                confidence_interval)

            # If the delta is significant, return
            if is_significant:
                if self.delta < 0:
                    if self.stable_test:
                        return REGRESSED if self.bigger_is_better else IMPROVED
                    else:
                        return UNSTABLE_REGRESSED if self.bigger_is_better else UNSTABLE_IMPROVED
                else:
                    if self.stable_test:
                        return IMPROVED if self.bigger_is_better else REGRESSED
                    else:
                        return UNSTABLE_IMPROVED if self.bigger_is_better else UNSTABLE_REGRESSED
            else:
                return UNCHANGED_PASS

        # Otherwise, report any changes above 0.2%, which is a rough
        # approximation for the smallest change we expect "could" be measured
        # accurately.
        if not ignore_small or abs(self.pct_delta) >= .002:
            if self.pct_delta < 0:
                if self.stable_test:
                    return REGRESSED if self.bigger_is_better else IMPROVED
                else:
                    return UNSTABLE_REGRESSED if self.bigger_is_better else UNSTABLE_IMPROVED
            else:
                if self.stable_test:
                    return IMPROVED if self.bigger_is_better else REGRESSED
                else:
                    return UNSTABLE_IMPROVED if self.bigger_is_better else UNSTABLE_REGRESSED
        else:
            return UNCHANGED_PASS


class RunInfo(object):
    def __init__(self, testsuite, runs_to_load,
                 aggregation_fn=stats.median, confidence_lv=.05,
                 only_tests=None, cv=[]):
        """Get all the samples needed to build a CR.
        runs_to_load are the run IDs of the runs to get the samples from.
        if only_tests is passed, only samples form those test IDs are fetched.
        """
        self.testsuite = testsuite
        self.aggregation_fn = aggregation_fn
        self.confidence_lv = confidence_lv

        self.sample_map = util.multidict()
        self.cv_sample_map = util.multidict()
        self.profile_map = dict()
        self.loaded_run_ids = set()
        self.loaded_cv_run_ids = set()

        self._load_samples_for_runs(runs_to_load, only_tests)
        self._load_cv_samples_for_runs(cv, only_tests)

    @property
    def test_ids(self):
        return set(key[1] for key in self.sample_map.keys())

    def get_sliding_runs(self, run, compare_run, num_comparison_runs=0):
        """
        Get num_comparison_runs most recent runs,
        This query is expensive.
        """
        runs = [run]
        runs_prev = self.testsuite.get_previous_runs_on_machine(run, num_comparison_runs)
        runs += runs_prev

        if compare_run is not None:
            compare_runs = [compare_run]
            comp_prev = self.testsuite.get_previous_runs_on_machine(compare_run, num_comparison_runs)
            compare_runs += comp_prev
        else:
            compare_runs = []

        return runs, compare_runs

    def get_run_comparison_result(self, run, compare_to, test_id, field,
                                  hash_of_binary_field, cv=False, stable_test=True):
        if compare_to is not None:
            compare_to = [compare_to]
        else:
            compare_to = []
        return self.get_comparison_result([run], compare_to, test_id, field,
                                          hash_of_binary_field, cv=cv, stable_test=stable_test)

    def get_samples(self, runs, test_id):
        all_samples = []
        for run in runs:
            samples = self.sample_map.get((run.id, test_id))
            if samples is not None:
                all_samples.extend(samples)
        return all_samples

    def get_cv_samples(self, runs, test_id):
        all_samples = []
        for run in runs:
            samples = self.cv_sample_map.get((run.id, test_id))
            if samples is not None:
                all_samples.extend(samples)
        return all_samples

    def get_comparison_result(self, runs, compare_runs, test_id, field,
                              hash_of_binary_field, cv=False, stable_test=True):
        # Get the field which indicates the requested field's status.
        status_field = field.status_field

        # Load the sample data for the current and previous runs and the
        # comparison window.
        if cv:
            run_samples = self.get_cv_samples(runs, test_id)
        else:
            run_samples = self.get_samples(runs, test_id)

        prev_samples = self.get_samples(compare_runs, test_id)

        cur_profile = prev_profile = None
        if runs:
            cur_profile = self.profile_map.get((runs[0].id, test_id), None)
        if compare_runs:
            prev_profile = self.profile_map.get((compare_runs[0].id, test_id), None)
        
        # Determine whether this (test,pset) passed or failed in the current and
        # previous runs.
        #
        # FIXME: Support XFAILs and non-determinism (mixed fail and pass)
        # better.
        run_failed = prev_failed = False
        if status_field:
            for sample in run_samples:
                run_failed |= sample[status_field.index] == FAIL
            for sample in prev_samples:
                prev_failed |= sample[status_field.index] == FAIL

        # Get the current and previous values.
        run_values = [s[field.index] for s in run_samples
                      if s[field.index] is not None]
        prev_values = [s[field.index] for s in prev_samples
                       if s[field.index] is not None]
        if hash_of_binary_field:
            hash_values = [s[hash_of_binary_field.index] for s in run_samples
                           if s[field.index] is not None]
            prev_hash_values = [s[hash_of_binary_field.index]
                                for s in prev_samples
                                if s[field.index] is not None]

            # All hash_values and all prev_hash_values should all be the same.
            # Warn in the log when the hash wasn't the same for all samples.
            cur_hash_set = set(hash_values)
            prev_hash_set = set(prev_hash_values)
            if len(cur_hash_set) > 1:
                logger.warning(("Found different hashes for multiple samples " +
                                "in the same run {0}: {1}").format(
                               runs, hash_values))
            if len(prev_hash_set) > 1:
                logger.warning(("Found different hashes for multiple samples " +
                                "in the same run {0}: {1}").format(
                               compare_runs, prev_hash_values))
            cur_hash = hash_values[0] if len(hash_values) > 0 else None
            prev_hash = prev_hash_values[0] \
                if len(prev_hash_values) > 0 else None
        else:
            cur_hash = None
            prev_hash = None
        r =  ComparisonResult(self.aggregation_fn,
                             run_failed, prev_failed, run_values,
                             prev_values, cur_hash, prev_hash,
                             cur_profile, prev_profile,
                             self.confidence_lv,
                             bigger_is_better=field.bigger_is_better,
                             stable_test=stable_test)
        return r

    def get_geomean_comparison_result(self, run, compare_to, field, tests):
        if tests:
            prev_values, run_values, prev_hash, cur_hash = zip(
                *[(cr.previous, cr.current, cr.prev_hash, cr.cur_hash)
                  for _, _, cr in tests
                  if cr.get_test_status() == UNCHANGED_PASS])
            prev_values = [x for x in prev_values if x is not None]
            run_values = [x for x in run_values if x is not None]
            prev_hash = [x for x in prev_hash if x is not None]
            cur_hash = [x for x in cur_hash if x is not None]
            prev_hash = prev_hash[0] if len(prev_hash) > 0 else None
            cur_hash = cur_hash[0] if len(cur_hash) > 0 else None
            prev_geo = calc_geomean(prev_values)
            prev_values = [prev_geo] if prev_geo else []
            run_values = [calc_geomean(run_values)]
        else:
            prev_values, run_values, prev_hash, cur_hash = [], [], None, None

        return ComparisonResult(self.aggregation_fn,
                                cur_failed=not bool(run_values),
                                prev_failed=not bool(prev_values),
                                samples=run_values,
                                prev_samples=prev_values,
                                cur_hash=cur_hash,
                                prev_hash=prev_hash,
                                confidence_lv=0,
                                bigger_is_better=field.bigger_is_better)

    def _load_samples_for_runs(self, run_ids, only_tests):
        # Find the set of new runs to load
        to_load = set(run_ids) - self.loaded_run_ids

        if to_load:
            # Batch load all of the samples for the needed runs.
            #
            # We speed things up considerably by loading the column data directly
            # here instead of requiring SA to materialize Sample objects.
            columns = [self.testsuite.Sample.run_id,
                       self.testsuite.Sample.test_id,
                       self.testsuite.Sample.profile_id]
            columns.extend(f.column for f in self.testsuite.sample_fields)

            q = self.testsuite.query(*columns)
            if only_tests:
                q = q.filter(self.testsuite.Sample.test_id.in_(only_tests))
            q = q.filter(self.testsuite.Sample.run_id.in_(to_load))
            for data in q:
                run_id = data[0]
                test_id = data[1]
                profile_id = data[2]
                sample_values = data[3:]
                self.sample_map[(run_id, test_id)] = sample_values
                if profile_id is not None:
                    self.profile_map[(run_id, test_id)] = profile_id

            self.loaded_run_ids |= to_load

    def _load_cv_samples_for_runs(self, run_ids, only_tests):
        # Find the set of new runs to load

        to_load = set(run_ids) - self.loaded_cv_run_ids

        if to_load:
            # Batch load all of the samples for the needed runs.
            #
            # We speed things up considerably by loading the column data directly
            # here instead of requiring SA to materialize Sample objects.
            columns = [self.testsuite.CVSample.run_id,
                       self.testsuite.CVSample.test_id,
                       self.testsuite.CVSample.profile_id]
            columns.extend(f.column for f in self.testsuite.cv_sample_fields)

            q = self.testsuite.query(*columns)
            if only_tests:
                q = q.filter(self.testsuite.CVSample.test_id.in_(only_tests))
            q = q.filter(self.testsuite.CVSample.run_id.in_(to_load))
            for data in q:
                run_id = data[0]
                test_id = data[1]
                profile_id = data[2]
                sample_values = data[3:]
                self.cv_sample_map[(run_id, test_id)] = sample_values
                if profile_id is not None:
                    self.profile_map[(run_id, test_id)] = profile_id

            self.loaded_cv_run_ids |= to_load