from urllib.parse import urlencode

from django.conf import settings
from django.shortcuts import render

from main_app.api import campaign_serializers
from main_app import models
from main_app import tasks
from cache_manager import CampaignCacheManager

from utils import shared_classes, shared_vars
from utils.logger import LogWriter


class CampaignRequestHandler(shared_classes.AbstractPropertySingleton):
    def __init__(self, client, template):
        self.client = client
        self.template = template
        self._cache_manager = CampaignCacheManager()
        self.logger_manager = LogWriter()

    @staticmethod
    def _get_valid_vast_headers():
        return {
            "Content-Type": "application/xml; charset = UTF-8",
            "connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        }

    def _check_level_1_no_ads(self, uid, level_1_id):
        return bool(self._cache_manager.get_user_level_1_no_ads(uid, level_1_id))

    def _check_level_2_no_ads(self, uid, level_2_id):
        return bool(self._cache_manager.get_user_level_2_no_ads(uid, level_2_id))

    def _get_empty_vast_response(self, request):
        """ Returns the Base Xml Template which contains empty tags """
        response = render(request, self.template, {})
        response.headers = self._get_valid_vast_headers()
        return response

    def prepare_vast_response(self, request, campaigns):
        """ Uses the given data and renders the Base xml Template """
        response = render(request, self.template, {"campaigns": campaigns})
        response.headers = self._get_valid_vast_headers()
        return response

    @staticmethod
    def get_log_data_from_serialized_campaign(campaign):
        return {"campaign_id": campaign["id"], "campaign_title": campaign["title"]}

    def _write_log(self, target, contents, is_json, **kwargs):
        self.logger_manager.perform_write_log(
            target=target,
            contents=contents,
            is_json=is_json,
            **kwargs
        )

    def _get_pre_and_mid_roll(self, request, queryset, uid, related_level_id, relation_level, params):
        related_campaigns = queryset.filter(
            related_level_id=related_level_id,
            relation_level=relation_level,
        )
        if related_campaigns.exists():
            pre_roll = (
                related_campaigns.filter(
                    campaign_type=models.CampaignRelation.CampaignType.PRE_ROLL
                ).order_by("?").last()
            )

            mid_roll = (
                related_campaigns.filter(
                    campaign_type=models.CampaignRelation.CampaignType.MID_ROLL
                ).order_by("?").last()
            )
            campaigns = tuple(
                filter(lambda relation: relation is not None, [pre_roll, mid_roll])
            )
            if campaigns:
                _serialized_data = campaign_serializers.CampaignRelationSerializer(
                    campaigns,
                    many=True,
                    context={"params": params, "request": request}
                ).data
                serialized_data = tuple(map(lambda item: dict(item), _serialized_data))
                tasks.perform_apply_campaign_limitation.apply_async(
                    args=(uid, serialized_data, self.client),
                    exchange=settings.CAMPAIGN_EXCHANGE,
                    routing_key=settings.CAMPAIGN_LIMITATION_ROUTING_KEY
                )
                return serialized_data

    def xml_response(self, request, uid, level_1_id, level_2_id, level_3_ids):
        """
        Checks limitations and sent fields at first if they were valid
        if hits the db and at first it checks level1 relations
        if there were no level1 relations it checks level2 relations
        if there were no level2 relations it checks level3 relations

            level_1 > level_2 > level_3

        """
        check_params = not any([level_1_id, level_2_id, level_3_ids])
        check_client = self.client is None
        check_level_1_no_ads = self._check_level_1_no_ads(uid, level_1_id)
        check_level_2_no_ads = self._check_level_2_no_ads(uid, level_2_id)

        if any(
                [check_params, check_level_1_no_ads, check_level_2_no_ads, check_client]
        ):
            return self._get_empty_vast_response(request)

        excluded_campaigns = self._cache_manager.get_user_and_campaign_exclude_list(uid)
        q_dict = {"uid": uid, "level_1_id": level_1_id, "level_2_id": level_2_id, "level_3_ids": level_3_ids}
        params = f"?{urlencode(q_dict)}"
        queryset = models.CampaignRelation.enable_objects.exclude(
            campaign_id__in=excluded_campaigns
        )
        if level_1_id is not None:
            related_campaigns_data = self._get_pre_and_mid_roll(
                request, queryset, uid, level_1_id, models.CampaignRelation.RelationLevel.LEVEL_1, params
            )
            if related_campaigns_data is not None:
                self._write_log(
                    target=shared_vars.USER_CAMPAIGN_LOGGER,
                    contents=related_campaigns_data,
                    is_json=True,
                    client=self.client,
                    uid=uid,
                    level_1_id=level_1_id,
                    level_2_id=level_2_id,
                    level_3_ids=level_3_ids,
                )
                return self.prepare_vast_response(
                    request,
                    related_campaigns_data
                )
        if level_2_id is not None:
            related_campaigns_data = self._get_pre_and_mid_roll(
                request, queryset, uid, level_2_id, models.CampaignRelation.RelationLevel.LEVEL_2, params
            )
            if related_campaigns_data:
                self._write_log(
                    target=shared_vars.USER_CAMPAIGN_LOGGER,
                    contents=related_campaigns_data,
                    is_json=True,
                    client=self.client,
                    uid=uid,
                    level_1_id=level_1_id,
                    level_2_id=level_2_id,
                    level_3_ids=level_3_ids,
                )
                return self.prepare_vast_response(
                    request,
                    related_campaigns_data
                )
        if level_3_ids:
            for level_3_id in level_3_ids:
                related_campaigns_data = self._get_pre_and_mid_roll(
                    request,
                    queryset,
                    uid,
                    level_3_id,
                    models.CampaignRelation.RelationLevel.LEVEL_3,
                    params
                )
                if related_campaigns_data:
                    self._write_log(
                        target=shared_vars.USER_CAMPAIGN_LOGGER,
                        contents=related_campaigns_data,
                        is_json=True,
                        client=self.client,
                        uid=uid,
                        level_1_id=level_1_id,
                        level_2_id=level_2_id,
                        level_3_ids=level_3_ids,
                    )
                    return self.prepare_vast_response(
                        request,
                        related_campaigns_data
                    )
        return self._get_empty_vast_response(request)
