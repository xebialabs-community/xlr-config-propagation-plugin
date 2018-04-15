from com.xebialabs.xlrelease.plugin.webhook import XmlPathResult
from com.xebialabs.deployit.plumbing import CurrentVersion
from com.xebialabs.deployit import ServerConfiguration
from xlrelease.HttpRequest import HttpRequest
from com.xebialabs.deployit.exception import NotFoundException
from itertools import groupby
import json
import re
import urllib


def push_configuration(connection_details, push_spec, dry_run, xlr_services):
    pusher = ConfigurationPusher(connection_details, push_spec, dry_run, xlr_services)
    return pusher.push_configuration()


def _get_parent(path):
    if '/' in path:
        return path.rsplit('/', 1)[0]
    return None


def _get_name(path):
    if '/' in path:
        return path.rsplit('/', 1)[1]
    return path


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

        # import templates one by one, rewriting JSONs with new imported IDs

        return {
            'warnings': self.warnings,
            'errors': self.errors,
            'actions': self.actions,

            'debug_template_details': templates_details,
            'debug_numbers': {
                'n_local_templates': n_local_templates,
                'n_with_remote_folder': n_with_remote_folder,
                'n_not_existing_remotely': n_not_existing_remotely
            }
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
        remote_folder_paths = set([_get_parent(t['remote_path'])
                                   for t in self._all_templates(templates_details)
                                   if _get_parent(t['remote_path'])])
        folder_ids_by_path = dict(map(
            lambda path: (path, self.remote_xlr.get_folder_id_by_path(path)),
            remote_folder_paths
        ))
        for template in self._all_templates(templates_details):
            folder_path = _get_parent(template['remote_path'])
            if folder_path:
                template['remote_folder_id'] = folder_ids_by_path[folder_path]
            else:
                template['remote_folder_id'] = 'Applications'

    def find_and_apply_remote_template_ids(self, templates_details):
        for template in self._all_templates(templates_details):
            remote_template_id = None
            if template.get('remote_folder_id', None):
                title = _get_name(template['remote_path'])
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
        templates_with_no_remote_folder = filter(lambda t: 'remote_folder_id' not in t, templates_details)
        missing_templates_count_by_path = {}
        for path, templates in groupby(templates_with_no_remote_folder, lambda t: _get_parent(t['remote_path'])):
            missing_templates_count_by_path[path] = len(templates)
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


# noinspection PyMethodMayBeStatic
class LocalXlr:
    def __init__(self, push_spec, xlr_services):
        self.push_spec = push_spec
        self.template_api = xlr_services['templateApi']
        self.folder_api = xlr_services['folderApi']
        self.configuration_api = xlr_services['configurationApi']
        self._folder_names_cache = {}
        self._configurations_details_cache = {}

    def get_local_xlr_details(self):
        return {
            'url': ServerConfiguration.getInstance().getServerUrl(),
            'version': CurrentVersion.get()
        }

    def get_templates_to_push(self):
        templates_spec = self.push_spec['templates']
        matching_templates_details = []
        page_size = 20
        page = 0
        while True:
            # title, tags, page, resultsPerPage, depth
            templates_page = self.template_api.getTemplates(None, None, page, page_size, 1000)

            templates_with_details = map(lambda t: (t, self._get_template_id_and_path(t)), templates_page)
            for (template, details) in templates_with_details:
                if self._matches_spec(details['path'], templates_spec):
                    details.update(self._get_template_references(template))
                    matching_templates_details.append(details)

            if len(templates_page) == 0:
                # no more templates
                break
            page += 1

        return matching_templates_details

    def _get_template_id_and_path(self, template):
        ci_id = self._normalize(template.getId())
        path = self._get_name_path(ci_id, template.getTitle())
        return {
            'id': ci_id,
            'path': path
        }

    def _get_template_references(self, template):
        referenced_configurations = self._get_referenced_configurations(template)
        referenced_templates = self._get_referenced_templates(template)
        return {
            'referenced_configurations': referenced_configurations,
            'referenced_templates': referenced_templates
        }

    def _get_name_path(self, ci_id, ci_path):
        parent_id = _get_parent(ci_id)
        if not parent_id or '/' not in parent_id:
            return ci_path  # stop on 'Applications'
        if parent_id in self._folder_names_cache:
            parent_name = self._folder_names_cache[parent_id]
        else:
            parent_name = self.folder_api.getFolder(parent_id).getTitle()
            self._folder_names_cache[parent_id] = parent_name
        return self._get_name_path(parent_id, parent_name + '/' + ci_path)

    def _matches_spec(self, path, spec):
        return any(map(
            # check for full match, so all characters should be part of the pattern
            lambda pattern: re.match(pattern, path) and len(re.match(pattern, path).group(0)) == len(path),
            spec['include']
        ))

    def _normalize(self, ci_id):
        return ci_id[1:] if ci_id.startswith('/') else ci_id

    def _get_referenced_configurations(self, template):
        referenced_configurations = []
        template_json = self._serialize(template)
        for config_id in set(re.findall('"Configuration/Custom/[\w/]+"', template_json)):
            config_id = config_id.replace('"', '')
            if config_id in self._configurations_details_cache:
                config_details = self._configurations_details_cache[config_id]
            else:
                config = self.configuration_api.getConfiguration(config_id)
                config_details = {
                    'id': config.getId(),
                    'type': config.getType().toString(),
                    'title': config.getTitle()
                }
                self._configurations_details_cache[config_id] = config_details
            referenced_configurations.append(config_details)
        return referenced_configurations

    def _get_referenced_templates(self, template):
        referenced_templates = []
        for task in template.getAllTasks():
            if task.getType().toString() == 'xlrelease.CreateReleaseTask' and task.getProperty('templateId'):
                referenced_template_id = task.getProperty('templateId')
                try:
                    referenced_template = self.template_api.getTemplate(referenced_template_id)
                    referenced_template_path = self._get_name_path(referenced_template_id,
                                                                   referenced_template.getTitle())
                    referenced_templates.append({
                        'id': referenced_template_id,
                        'path': referenced_template_path,
                        'from_task_id': task.getId()
                    })
                except NotFoundException as e:
                    print('WARN: could not find template by ID [%s] referenced by task [%s](%s) '
                          'of template [%s](%s): %s' % (referenced_template_id, task.getTitle(), task.getId(),
                                                        template.getTitle(), template.getId(), e))
        return referenced_templates

    # noinspection PyProtectedMember
    def _serialize(self, ci):
        from com.xebialabs.xlrelease.json import CiSerializerHelper
        if ci._delegate:
            return CiSerializerHelper.serialize(ci._delegate)
        else:
            return CiSerializerHelper.serialize(ci)


class RemoteXlr:
    def __init__(self, server, username, password):
        self.server = server
        self.username = username
        self.password = password
        self.folder_path_to_id_cache = {}
        self.configuration_type_title_to_id_cache = {}
        self.folder_id_to_template_title_to_id_cache = {}

    def get_xlr_details(self):
        response = self._request().get('/server/info', contentType='application/xml')
        if response.isSuccessful():
            version = XmlPathResult(response.response, '/server-info/version').get()
            return {
                'url': self.server['url'],
                'version': version
            }
        else:
            response.errorDump()
            raise Exception('Version request to /server/info failed with status %d' % response.getStatus())

    def get_folder_id_by_path(self, path):
        if path in self.folder_path_to_id_cache:
            return self.folder_path_to_id_cache[path]
        query = '?byPath=%s' % urllib.quote(path)
        response = self._request().get('/api/v1/folders/find' + query, contentType='application/json')
        if response.getStatus() == 200:
            folder_id = json.loads(response.response)['id']
        elif response.getStatus() == 404:
            folder_id = None
        else:
            response.errorDump()
            raise Exception('Request to find a folder [%s] failed with status %d' % (path, response.getStatus()))
        self.folder_path_to_id_cache[path] = folder_id
        return folder_id

    def get_configuration_id_by_type_and_title(self, config_type, config_title, warnings):
        path = '%s/%s' % (config_type, config_title)
        if path in self.configuration_type_title_to_id_cache:
            return self.configuration_type_title_to_id_cache[path]
        query = '?configurationType=%s&title=%s' % (urllib.quote(config_type), urllib.quote(config_title))
        response = self._request().get('/api/v1/config/byTypeAndTitle' + query, contentType='application/json')
        if response.getStatus() == 200:
            configurations = json.loads(response.response)
            if not configurations:
                configuration_id = None
            else:
                if len(configurations) > 1:
                    warnings.append('Found %d configurations by type [%s] and title [%s], choosing the first from: %s' %
                                    (len(configurations), config_type, config_title, [c['id'] for c in configurations]))
                configuration_id = configurations[0]['id']
        else:
            response.errorDump()
            raise Exception('Request to find a configuration [%s/%s] failed with status %d' %
                            (config_type, config_title, response.getStatus()))
        self.configuration_type_title_to_id_cache[path] = configuration_id
        return configuration_id

    def get_template_id_by_folder_and_title(self, folder_id, title, warnings):
        if folder_id in self.folder_id_to_template_title_to_id_cache:
            return self.folder_id_to_template_title_to_id_cache[folder_id].get(title, None)

        # Unfortunately there's no public API to search for a template by folder _and_ title,
        # so iterate through all templates of a folder and cache them
        template_titles_to_ids = {}
        context = '/api/v1/folders/%s/templates' % folder_id
        results_per_page = 20
        page = 0
        while True:
            query = '?page=%d&resultsPerPage=%d&depth=1' % (page, results_per_page)
            response = self._request().get(context + query, contentType='application/json')
            if response.getStatus() == 200:
                templates = json.loads(response.response)
            else:
                response.errorDump()
                raise Exception('Request to get a page %d of templates of folder [%s] failed with status %d' %
                                (page, folder_id, response.getStatus()))
            if not templates:
                # pagination finished
                break
            for template in templates:
                if template['title'] in template_titles_to_ids:
                    warnings.append('Found more than one template by title [%s] in remote folder [%s], choosing '
                                    'the first one: [%s]' % (template['title'], folder_id,
                                                             template_titles_to_ids[template['title']]))
                else:
                    template_titles_to_ids[template['title']] = template['id']
            page += 1

        self.folder_id_to_template_title_to_id_cache[folder_id] = template_titles_to_ids
        return self.folder_id_to_template_title_to_id_cache[folder_id].get(title, None)

    def _request(self):
        return HttpRequest(self.server, self.username, self.password)


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
