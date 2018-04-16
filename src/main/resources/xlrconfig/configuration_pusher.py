from local_xlr import LocalXlr
from remote_xlr import RemoteXlr
from itertools import groupby
from xlrconfig import get_parent, get_name
import re


def push_configuration(connection_details, push_spec, dry_run, xlr_services):
    pusher = ConfigurationPusher(connection_details, push_spec, dry_run, xlr_services)
    return pusher.push_configuration()


# noinspection PyTypeChecker,PyMethodMayBeStatic
class ConfigurationPusher:
    def __init__(self, connection_details, push_spec, dry_run, xlr_services):
        self.local_xlr = LocalXlr(push_spec, xlr_services)
        self.remote_xlr = RemoteXlr(*connection_details)
        self.push_spec = push_spec
        self.dry_run = dry_run
        self.warnings = []
        self.errors = []
        self.actions = []
        self.stats = {}

    def push_configuration(self):
        source_xlr = self.local_xlr.get_local_xlr_details()
        target_xlr = self.remote_xlr.get_xlr_details()
        print('Going to push configuration from XL Release %s (%s) to XL Release %s (%s)' % (
            source_xlr['version'], source_xlr['url'], target_xlr['version'], target_xlr['url']
        ))

        # find templates that were requested to be pushed
        templates_details = self.local_xlr.get_templates_to_push()
        n_local_templates = len(templates_details)

        self.apply_folder_renamings(templates_details)
        self.apply_configuration_renamings(templates_details)

        # find corresponding remote entities
        self.find_and_apply_remote_folder_ids(templates_details)
        self.find_and_apply_remote_template_ids(templates_details)
        self.find_and_apply_remote_configuration_ids(templates_details)

        # check if all folders are present on the target instance
        templates_details = self.filter_by_present_remote_folder(templates_details)
        n_with_remote_folder = len(templates_details)

        # check if all configurations are present on the target instance,
        self.report_missing_configurations(templates_details)

        # check for templates already present on the target system,
        templates_details = self.filter_by_absent_templates(templates_details)
        n_not_existing_remotely = len(templates_details)

        # check if all referenced CreateReleaseTask templates are present on the
        # target instance or are going to be pushed
        self.report_missing_referenced_templates(templates_details)

        # sort templates first-dependent-then-depending order
        TopologicalSorter(templates_details, self.warnings).sort()

        for template in templates_details:
            self.actions.append({
                'type': 'import',
                'description': 'Import template [%s] to the remote instance' % template['path'],
                'entity': template
            })

        # import templates one by one, rewriting JSONs with new imported IDs
        self.stats = {
            'n_matched_templates': n_local_templates,
            'n_with_remote_folder': n_with_remote_folder,
            'n_not_existing_remotely': n_not_existing_remotely
        }
        if not self.dry_run:
            print('Prepared the execution plan of %d actions, start executing' % len(self.actions))
            self.execute_actions()
            print('Finished the execution, pushed %d templates to the remote instance out of %d matched local ones' %
                  (self.stats['n_imported'], n_local_templates))
        else:
            print('Skipping execution of %d actions as it is dry run' % len(self.actions))

        return {
            # 'debug_template_details': templates_details
            'warnings': self.warnings,
            'errors': self.errors,
            'actions': self.actions,
            'stats': self.stats,
        }

    def _all_templates(self, templates_details):
        return templates_details + [ref for t in templates_details for ref in t['referenced_templates']]

    def apply_folder_renamings(self, templates_details):
        renamings = self.push_spec.get('folders', {}).get('rename', {})
        for template in self._all_templates(templates_details):
            local_path = template['path']
            remote_path = local_path
            for pattern_to_rename in renamings:
                pattern = '^' + pattern_to_rename
                if re.match(pattern, local_path):
                    remote_path = re.sub(pattern, renamings[pattern_to_rename], local_path)
                    break  # don't go through several renamings
            template['remote_path'] = remote_path

    def apply_configuration_renamings(self, templates_details):
        configs = [config for t in templates_details for config in t['referenced_configurations']]
        renamings = self.push_spec.get('configurations', {}).get('rename', {})
        for config in configs:
            title = config['title']
            remote_title = renamings.get('%s/%s' % ((config['type']), title), title)
            config['remote_title'] = remote_title

    def find_and_apply_remote_folder_ids(self, templates_details):
        remote_folder_paths = set([get_parent(t['remote_path'])
                                   for t in self._all_templates(templates_details)
                                   if get_parent(t['remote_path'])])
        folder_ids_by_path = dict(map(
            lambda path: (path, self.remote_xlr.get_folder_id_by_path(path)),
            remote_folder_paths
        ))
        for template in self._all_templates(templates_details):
            folder_path = get_parent(template['remote_path'])
            if folder_path:
                template['remote_folder_id'] = folder_ids_by_path[folder_path]
            else:
                template['remote_folder_id'] = 'Applications'

    def find_and_apply_remote_template_ids(self, templates_details):
        for template in self._all_templates(templates_details):
            remote_template_id = None
            if template.get('remote_folder_id', None):
                title = get_name(template['remote_path'])
                remote_template_id = self.remote_xlr.get_template_id_by_folder_and_title(
                    template['remote_folder_id'], title, self.warnings)
            template['remote_template_id'] = remote_template_id

    def find_and_apply_remote_configuration_ids(self, templates_details):
        all_configurations = [config for t in templates_details for config in t['referenced_configurations']]
        unique_configurations = dict([(config['id'], config) for config in all_configurations])
        remote_configurations = {}
        for local_config_id, config in unique_configurations.items():
            remote_config_id = self.remote_xlr.get_configuration_id_by_type_and_title(
                config['type'], config['remote_title'], self.warnings)
            remote_configurations[local_config_id] = remote_config_id
        for config in all_configurations:
            config['remote_configuration_id'] = remote_configurations[config['id']]

    def filter_by_present_remote_folder(self, templates_details):
        templates_with_no_remote_folder = filter(lambda t: not t['remote_folder_id'], templates_details)
        missing_templates_count_by_path = {}
        for path, templates in groupby(templates_with_no_remote_folder, lambda t: get_parent(t['remote_path'])):
            missing_templates_count_by_path[path] = len(list(templates))
        missing_paths = missing_templates_count_by_path.keys()
        missing_paths.sort()
        for path in missing_paths:
            self.errors.append('Missing remote folder [%s] for %d matching templates' %
                               (path, missing_templates_count_by_path.get(path, 0)))
        # return only the present ones
        return filter(lambda t: t['remote_folder_id'], templates_details)

    def report_missing_configurations(self, templates_details):
        all_configurations = [config for t in templates_details for config in t['referenced_configurations']]
        missing_configurations = dict([(config['id'], config)
                                       for config in all_configurations
                                       if not config['remote_configuration_id']])
        for local_id, config in missing_configurations.items():
            self.warnings.append('Missing remote configuration by type [%s] and title [%s]' %
                                 (config['type'], config['title']))

    def filter_by_absent_templates(self, templates_details):
        for template in templates_details:
            if template['remote_template_id']:
                self.actions.append({
                    'type': 'noop',
                    'description': 'Template [%s](%s) already exists on the remote instance: [%s](%s)' % (
                        template['path'], template['id'], template['remote_path'], template['remote_template_id'])
                })
        # return only non-existing ones
        return filter(lambda t: not t['remote_template_id'], templates_details)

    def report_missing_referenced_templates(self, templates_details):
        pushed_template_ids = set(t['id'] for t in templates_details)
        referenced_templates_by_ids = dict([(ref['id'], ref)
                                            for t in templates_details
                                            for ref in t['referenced_templates']])
        external_missing_templates = [t for t_id, t in referenced_templates_by_ids.items()
                                      if not t['remote_template_id'] and t_id not in pushed_template_ids]
        for missing in external_missing_templates:
            templates_using_it = [t for t in templates_details
                                  if missing['id'] in
                                  [ref['id'] for ref in t['referenced_templates']]]
            self.warnings.append('Missing remote template [%s] referenced from %d local templates: %s' % (
                missing['remote_path'], len(templates_using_it), [t['path'] for t in templates_using_it]
            ))

    def execute_actions(self):
        template_id_to_imported_id = {}
        n_imported = 0
        n_failed_import = 0
        for action in self.actions:
            if action['type'] == 'noop':
                continue
            if action['type'] == 'import':
                template_details = action['entity']
                # fill in referenced template remote id if needed
                for ref in template_details['referenced_templates']:
                    if not ref['remote_template_id']:
                        ref['remote_template_id'] = template_id_to_imported_id.get(ref['id'], None)
                try:
                    imported_id = self.import_template(template_details)
                    template_id_to_imported_id[template_details['id']] = imported_id
                    template_details['remote_template_id'] = imported_id
                    n_imported += 1
                except Exception as e:
                    self.errors.append('Could not import template [%s](%s): %s' % (
                        template_details['path'], template_details['id'], e))
                    n_failed_import += 1

        self.stats['n_imported'] = n_imported
        self.stats['n_failed_import'] = n_failed_import

    def import_template(self, template_details):
        template = self.local_xlr.get_template(template_details['id'])
        self.local_xlr.strip_attachments_and_warn(template, self.warnings)
        self.local_xlr.check_triggers_and_warn(template, self.warnings)
        template_json = self.local_xlr.to_json(template)

        def replace_ids(t_json, local_id, remote_id):
            return t_json.replace('"%s"' % local_id, '"%s"' % remote_id)

        # rewrite configuration IDs
        for config in template_details['referenced_configurations']:
            if config['remote_configuration_id']:
                template_json = replace_ids(template_json, config['id'], config['remote_configuration_id'])
        # rewrite referenced template IDs
        for ref in template_details['referenced_templates']:
            if ref['remote_template_id']:
                template_json = replace_ids(template_json, ref['id'], ref['remote_template_id'])

        imported_id = self.remote_xlr.import_template(template_details['remote_folder_id'], template_json,
                                                      template_details['path'], self.warnings)
        return imported_id


class TopologicalSorter:
    def __init__(self, templates_details, warnings):
        self.templates_details = templates_details
        self.warnings = warnings

    def sort(self):
        templates_by_ids = dict([(t['id'], t) for t in self.templates_details])
        ordered_ids = []
        stack = []

        for template_id in templates_by_ids:
            if template_id in ordered_ids:
                continue  # already visited by a previous DFS run
            # Run DFS algorithm and push to ordered_ids when exiting a tree branch
            visited = set()
            stack.append(template_id)
            while stack:
                node_id = stack[-1]
                children_ids = [t['id'] for t in templates_by_ids[node_id]['referenced_templates']]
                children_to_visit = set(children_ids) - visited
                if children_to_visit:
                    child_id = next(iter(children_to_visit))
                    if child_id in stack:
                        # cycle detected
                        self.warnings.append('There is a cycle CreateReleaseTask dependency between templates '
                                             '[%s] and [%s], so you will have to restore the link manually after the '
                                             'configuration has been pushed' % (node_id, child_id))
                    elif child_id not in templates_by_ids:
                        # encountered an external template reference, skipping
                        visited.add(child_id)
                    else:
                        stack.append(child_id)
                else:
                    # all children visited, go up the tree
                    stack.pop()
                    visited.add(node_id)
                    ordered_ids.append(node_id)

        # sort templates_details according to the topological order
        self.templates_details.sort(key=lambda template: ordered_ids.index(template['id']))
