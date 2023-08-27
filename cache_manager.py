import time

from django.conf import settings

from utils import shared_classes
from utils.redis_backend import RedisGateway


class CampaignCacheManager(shared_classes.AbstractSingleton):
    """
    This class manages all cache performance [insert, get, modify, etc ...]
    using custom cache gateway
    """

    def __init__(self):
        # ---------------------------------------- REDIS GATEWAYS ------------------------------------------------------
        self._counters_gateway = RedisGateway(settings.REDIS_COUNTERS_CACHE_PREFIX)
        self._exclude_lists_gateway = RedisGateway(
            settings.REDIS_EXCLUDE_LISTS_CACHE_PREFIX
        )
        self._no_ads_gateway = RedisGateway(settings.REDIS_NO_ADS_CACHE_PREFIX)

        # --------------------------------------- COUNTER CACHE KEYS ---------------------------------------------------
        self._campaign_total_show_count_cache_key = "campaign_{}_total_show_count"
        self._campaign_total_show_count_insertion_timestamp_cache_key = "campaign_{}_total_show_count_timestamp"
        self._user_campaign_show_count_cache_key = "user_{}_campaign_{}_show_count"
        self._user_level_1_campaign_show_counter_cache_key = (
            "user_{}_level_1_{}_show_count"
        )
        self._user_level_2_campaign_watch_time_counter_cache_key = (
            "user_{}_level_2_{}_watch_time"
        )

        # --------------------------------------- EXCLUDE CACHE KEYS ---------------------------------------------------
        self._exclude_campaigns_cache_key = "excluded_campaigns"
        self._user_excluded_campaigns_cache_key = "user_{}_excluded_campaigns"

        # ---------------------------------------- NO ADS CACHE KEYS ---------------------------------------------------
        self._user_level_1_no_ads_cache_key = "user_{}_level_1_{}_no_ads"
        self._user_level_2_no_ads_cache_key = "user_{}_level_2_{}_no_ads"

    # ========================================== SCORE CALCULATORS =====================================================
    @staticmethod
    def _get_current_timestamp():
        return int(time.time())

    @staticmethod
    def _get_days_seconds(days):
        if days is not None:
            return days * 3600 * 24

    @staticmethod
    def _get_hours_seconds(hours):
        if hours is not None:
            return hours * 3600

    def _get_days_score(self, days=1):
        if days is None:
            return "inf"
        return self._get_current_timestamp() + self._get_days_seconds(days)

    def _get_hours_score(self, hours=1):
        if hours is None:
            return "inf"
        return self._get_current_timestamp() + self._get_hours_seconds(hours)

    # ============================================= RETRIEVE DATA ======================================================
    #                    ------------------------- GET EXCLUDE LISTS --------------------------
    def _get_exclude_campaigns_list(self):
        return self._exclude_lists_gateway.zget(
            ordered_set_name=self._exclude_campaigns_cache_key,
            min_score=self._get_current_timestamp(),
            max_score="inf",
            cast=int,
            rm_out_range=True,
        )

    def _get_user_exclude_campaigns_list(self, uid):
        return self._exclude_lists_gateway.zget(
            ordered_set_name=self._user_excluded_campaigns_cache_key.format(uid),
            min_score=self._get_current_timestamp(),
            max_score="inf",
            cast=int,
            rm_out_range=True,
        )

    #                    ------------------------- SET EXCLUDE LISTS --------------------------
    def _set_exclude_campaigns(self, campaign_id, timeout):
        self._exclude_lists_gateway.zadd(
            ordered_set_name=self._exclude_campaigns_cache_key,
            key=campaign_id,
            score=timeout,
        )

    def _set_user_exclude_campaigns(self, uid, campaign_id, timeout):
        self._exclude_lists_gateway.zadd(
            ordered_set_name=self._user_excluded_campaigns_cache_key.format(uid),
            key=campaign_id,
            score=timeout,
        )

    #                    ---------------------------- GET NO ADS ------------------------------
    def _get_user_level_1_no_ads(self, uid, level_1_id):
        return self._no_ads_gateway.get(
            key=self._user_level_1_no_ads_cache_key.format(uid, level_1_id), cast=int
        )

    def _get_user_level_2_no_ads(self, uid, level_2_id):
        return self._no_ads_gateway.get(
            key=self._user_level_2_no_ads_cache_key.format(uid, level_2_id), cast=int
        )

    #                    ---------------------------- SET NO ADS ------------------------------
    def _set_user_level_1_no_ads(self, uid, level_1_id, timeout):
        self._no_ads_gateway.set(
            self._user_level_1_no_ads_cache_key.format(uid, level_1_id),
            1,
            timeout=self._get_hours_seconds(timeout),
        )

    def _set_user_level_2_no_ads(self, uid, level_2_id, timeout):
        self._no_ads_gateway.set(
            self._user_level_2_no_ads_cache_key.format(uid, level_2_id),
            1,
            timeout=self._get_hours_seconds(timeout),
        )

    #                    --------------------------- GET COUNTERS -----------------------------
    def _get_campaign_total_show_count(self, campaign_id):
        return self._counters_gateway.get(
            self._campaign_total_show_count_cache_key.format(campaign_id),
            cast=int,
        )

    def _get_user_campaign_show_count(self, uid, campaign_id):
        return self._counters_gateway.get(
            self._user_campaign_show_count_cache_key.format(uid, campaign_id),
            cast=int,
        )

    def _get_user_level_1_show_count(self, uid, level_1_id):
        return self._counters_gateway.get(
            self._user_level_1_campaign_show_counter_cache_key.format(uid, level_1_id),
            cast=int,
        )

    def _get_user_level_2_watch_time_count(self, uid, level_2_id):
        return self._counters_gateway.get(
            self._user_level_2_campaign_watch_time_counter_cache_key.format(
                uid, level_2_id
            ),
            cast=int,
        )

    #                    --------------------------- SET COUNTERS -----------------------------
    def _set_campaign_total_show_counter(self, campaign_id, timeout, max_allowed):
        key = self._campaign_total_show_count_cache_key.format(campaign_id)
        insertion_timestamp_key = self._campaign_total_show_count_insertion_timestamp_cache_key.format(campaign_id)
        if self._counters_gateway.get(key, cast=int) is None:
            self._counters_gateway.set(insertion_timestamp_key, self._get_current_timestamp())
        count = self._counters_gateway.incr(
            key=key,
            timeout=self._get_days_seconds(timeout),
        )
        if count >= max_allowed:
            blocking_timeout = self._counters_gateway.get_key_ttl(key) + self._get_current_timestamp()
            self._set_exclude_campaigns(campaign_id, blocking_timeout)

    def _set_user_campaign_show_counter(self, uid, campaign_id, timeout, max_allowed):
        key = self._user_campaign_show_count_cache_key.format(uid, campaign_id)
        count = self._counters_gateway.incr(
            key=key,
            timeout=self._get_hours_seconds(timeout),
        )
        if count >= max_allowed:
            blocking_timeout = self._counters_gateway.get_key_ttl(key) + self._get_current_timestamp()
            self._set_user_exclude_campaigns(uid, campaign_id, blocking_timeout)

    def _set_user_level_1_show_counter(self, uid, level_1_id, timeout, max_allowed):
        count = self._counters_gateway.incr(
            key=self._user_level_1_campaign_show_counter_cache_key.format(
                uid, level_1_id
            ),
            timeout=self._get_hours_seconds(timeout),
        )
        if count >= max_allowed:
            self._set_user_level_1_no_ads(uid, level_1_id, timeout)

    def _set_user_level_2_watch_time_counter(
            self, uid, level2_id, watch_time, timeout, max_allowed
    ):
        if self.get_user_level_2_no_ads(uid, level2_id) is not None:
            return
        count = self._counters_gateway.incr(
            self._user_level_2_campaign_watch_time_counter_cache_key.format(
                uid, level2_id
            ),
            value=watch_time,
            timeout=self._get_hours_seconds(timeout),
        )
        if count >= max_allowed.seconds:
            self._set_user_level_2_no_ads(uid, level2_id, timeout)

    # =========================================== REACHABLE FUNCTIONS ==================================================
    def get_exclude_campaigns_list(self):
        return self._get_exclude_campaigns_list()

    def get_user_exclude_campaigns_list(self, uid: str):
        if uid is not None:
            return self._get_user_exclude_campaigns_list(uid)
        return tuple()

    def get_user_and_campaign_exclude_list(self, uid: str):
        if uid is not None:
            return (
                    self._get_exclude_campaigns_list()
                    + self._get_user_exclude_campaigns_list(uid)
            )
        return tuple()

    def get_user_level_1_no_ads(self, uid: str, level_1_id: int):
        if uid is not None:
            return self._get_user_level_1_no_ads(uid, level_1_id)

    def get_user_level_2_no_ads(self, uid: str, level_2_id: int):
        if uid is not None:
            return self._get_user_level_2_no_ads(uid, level_2_id)

    def set_campaign_total_show_counter(
            self, campaign_id: int, timeout: int, max_allowed: int
    ):
        return self._set_campaign_total_show_counter(campaign_id, timeout, max_allowed)

    def set_user_campaign_show_counter(
            self, uid: str, campaign_id: int, timeout: int, max_allowed: int
    ):
        if uid is not None:
            return self._set_user_campaign_show_counter(
                uid, campaign_id, timeout, max_allowed
            )

    def set_user_level_1_show_counter(
            self, uid: str, level_1_id: int, timeout: int, max_allowed: int
    ):
        if uid is not None:
            return self._set_user_level_1_show_counter(
                uid, level_1_id, timeout, max_allowed
            )

    def set_user_level_2_watch_time_counter(
            self, uid: str, level2_id: int, watch_time: int, timeout: int, max_allowed
    ):
        if uid is not None:
            return self._set_user_level_2_watch_time_counter(
                uid, level2_id, watch_time, timeout, max_allowed
            )

    def get_campaign_counter_timestamp(self, campaign_id):
        key = self._campaign_total_show_count_insertion_timestamp_cache_key.format(campaign_id)
        return self._counters_gateway.get(key, int)
