#!/usr/bin/python3
# coding=utf-8

#   Copyright 2025 EPAM Systems
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

""" Method """

import time
from copy import deepcopy

from pylon.core.tools import log  # pylint: disable=E0611,E0401,W0611
from pylon.core.tools import web  # pylint: disable=E0611,E0401,W0611
from sqlalchemy.orm.attributes import flag_modified

from tools import db
from ..models.configuration import Configuration


class Method:  # pylint: disable=E1101,R0903,W0201
    """
        Method Resource

        self is pointing to current Module instance

        web.method decorator takes zero or one argument: method name
        Note: web.method decorator must be the last decorator (at top)
    """

    # pylint: disable=R,W0613
    @web.method()
    def migrate_configuration_data_alita_title(self, *args, **kwargs):
        """Rename 'alita_title' to 'elitea_title' in configuration data JSONB. Param: project_id=<all|N>[;dry_run]

        Configuration records (llm_model, embedding, image_generation, etc.) store
        credential references in data->ai_credentials as
        {"alita_title": "...", "private": ...}. After the EliteaAI debranding the
        expand_configuration function looks for 'elitea_title', so these legacy
        references fail to resolve.

        This task walks every Configuration row, inspects all nested dicts inside
        data, and renames any 'alita_title' key to 'elitea_title'.

        Idempotent: safe to run multiple times — skips objects that already use
        'elitea_title' or don't contain 'alita_title'.

        Param format (required):
            "project_id=<all|N>[;dry_run]"

        Examples:
            "project_id=all;dry_run"  - dry run across all projects
            "project_id=all"          - migrate all projects
            "project_id=3"            - migrate project 3 only
        """
        param = kwargs.get("param", "") or ""
        dry_run = False
        project_id_filter = None
        project_id_found = False

        for seg in [s.strip() for s in param.split(";")]:
            seg_lower = seg.lower()
            if seg_lower.startswith("project_id="):
                project_id_found = True
                value = seg[len("project_id="):].strip()
                if value.lower() != "all":
                    try:
                        project_id_filter = int(value)
                    except ValueError:
                        log.error("migrate_configuration_data_alita_title: invalid project_id '%s'", value)
                        return {"migrated": 0, "error": f"invalid project_id: '{value}'"}
            elif seg_lower == "dry_run":
                dry_run = True

        if not project_id_found:
            log.error("migrate_configuration_data_alita_title: project_id= is required. Format: project_id=<all|N>[;dry_run]")
            return {"migrated": 0, "error": "project_id= is required. Format: project_id=<all|N>[;dry_run]"}

        prefix = "[DRY RUN] " if dry_run else ""
        log.info("Starting migrate_configuration_data_alita_title (dry_run=%s, project_id_filter=%s)", dry_run, project_id_filter)
        start_ts = time.time()
        total_migrated = 0

        try:
            if project_id_filter is not None:
                projects = [{"id": project_id_filter}]
            else:
                projects = self.context.rpc_manager.call.project_list() or []
        except Exception:  # pylint: disable=W0703
            log.exception("migrate_configuration_data_alita_title: failed to list projects")
            return {"migrated": 0, "error": "failed to list projects"}

        def _rename_alita_keys(obj):
            """Recursively rename 'alita_title' -> 'elitea_title' in nested dicts.
            Returns True if any change was made."""
            changed = False
            if not isinstance(obj, dict):
                return changed
            if 'alita_title' in obj and 'elitea_title' not in obj:
                obj['elitea_title'] = obj.pop('alita_title')
                changed = True
            for val in obj.values():
                if isinstance(val, dict):
                    changed = _rename_alita_keys(val) or changed
            return changed

        for project in projects:
            project_id = project['id']
            try:
                with db.with_project_schema_session(project_id) as session:
                    configs = session.query(Configuration).all()

                    for cfg in configs:
                        if not cfg.data:
                            continue

                        updated_data = deepcopy(cfg.data)
                        if _rename_alita_keys(updated_data):
                            total_migrated += 1
                            log.info(
                                "%sproject %s, configuration id=%s (%s/%s) elitea_title=%s: "
                                "alita_title -> elitea_title in data",
                                prefix, project_id, cfg.id, cfg.section, cfg.type, cfg.elitea_title
                            )
                            if not dry_run:
                                cfg.data = updated_data
                                flag_modified(cfg, 'data')

                    if not dry_run:
                        session.commit()

            except Exception:  # pylint: disable=W0703
                log.exception(
                    "%smigrate_configuration_data_alita_title: error in project %s", prefix, project_id
                )

        end_ts = time.time()
        log.info(
            "%sExiting migrate_configuration_data_alita_title — %s %s configuration(s) (duration = %ss)",
            prefix, "would migrate" if dry_run else "migrated", total_migrated, round(end_ts - start_ts, 2)
        )
        return {"migrated": total_migrated, "dry_run": dry_run}
