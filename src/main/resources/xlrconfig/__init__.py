from com.xebialabs.xlrelease.plugin.webhook import XmlPathResult
from com.xebialabs.deployit.plumbing import CurrentVersion
from com.xebialabs.deployit import ServerConfiguration
from xlrelease.HttpRequest import HttpRequest
from com.xebialabs.deployit.exception import NotFoundException
import json
import re
import urllib


def push_configuration(connection_details, push_spec, dry_run, xlr_services):
    pusher = ConfigurationPusher(connection_details, push_spec, dry_run, xlr_services)
    return pusher.push_configuration()


def _get_parent(path):
    if '/' in path:
        return path.rsplit('/', 1)[0]
    else:
        return None


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

        templates_details = self.local_xlr.get_templates_to_push()
        n_local_templates = len(templates_details)

        # check if all folders are present on the target instance
        remote_folders = self.find_remote_folders(templates_details)
        templates_details = self.filter_and_report_by_remote_folders(templates_details, remote_folders)
        n_no_remote_folder = n_local_templates - len(templates_details)

        # check if all configurations are present on the target instance,
        # warn about missing ones
        local_configurations = self.get_local_configurations(templates_details)
        remote_configurations = self.find_remote_configurations(local_configurations)
        self.report_missing_configurations(local_configurations, remote_configurations)

        # check for templates already present on the target system,
        # warn that they won't be updated yet in this version of the plugin, remove from the sync list

        # check if all referenced templates are present on the target instance
        # warn about missing ones

        # sort template topologically

        # import templates one by one, rewriting JSONs with new imported IDs

        return {
            'warnings': self.warnings,
            'errors': self.errors,
            'actions': self.actions,

            'debug_template_details': templates_details,
            'debug_remote_folders': remote_folders,
            'debug_remote_configurations': remote_configurations
        }

    def find_remote_folders(self, templates_details):
        # collect expected remote folder paths
        local_folder_paths = list(set([_get_parent(t['path']) for t in templates_details if _get_parent(t['path'])]))
        local_folder_paths.sort()
        renamings = self.push_spec.get('folders', {}).get('rename', {})
        local_to_remote_path = dict(zip(local_folder_paths, local_folder_paths))
        for local_path in local_folder_paths:
            for pattern_to_rename in renamings:
                pattern = '^' + pattern_to_rename
                if re.match(pattern, local_path):
                    remote_path = re.sub(pattern, renamings[pattern_to_rename], local_path)
                    local_to_remote_path[local_path] = remote_path
                    break  # don't go through several renamings

        # search for remote folders and save their IDs
        return dict(map(
            lambda path: (path, self.remote_xlr.get_folder_id_by_path(local_to_remote_path[path])),
            local_folder_paths
        ))

    def get_local_configurations(self, templates_details):
        return dict([(config['id'], config)
                     for template in templates_details
                     for config in template['referenced_configurations']])

    def find_remote_configurations(self, local_configurations):
        # collect expected remote configurations
        remote_configurations = {}
        renamings = self.push_spec.get('configurations', {}).get('rename', {})
        for (local_config_id, local_config) in local_configurations.items():
            config_type = local_config['type']
            local_title = local_config['title']
            remote_title = renamings.get('%s/%s' % (config_type, local_title), local_title)
            remote_config_id = self.remote_xlr.get_configuration_id_by_type_and_title(
                config_type, remote_title, self.warnings)
            remote_configurations[local_config_id] = remote_config_id
        return remote_configurations

    def filter_and_report_by_remote_folders(self, templates_details, remote_folders):
        template_ids_with_no_remote_folder = []
        template_count_by_path = {}

        # add remote folder id where present and count where absent
        for template in templates_details:
            folder_path = _get_parent(template['path'])
            if folder_path:
                # a folder template
                if remote_folders[folder_path]:
                    template['remote_folder_id'] = remote_folders[folder_path]
                else:
                    template_ids_with_no_remote_folder.append(template['id'])
                    count = template_count_by_path.get(folder_path, 0)
                    template_count_by_path[folder_path] = count + 1
            else:
                # a root template
                template['remote_folder_id'] = '/'

        # report the absent ones
        missing_paths = [path for (path, folder_id) in remote_folders.items() if not folder_id]
        if missing_paths:
            missing_paths.sort()
            for path in missing_paths:
                self.errors.append('Missing remote folder [%s] for %d matching templates' %
                                   (path, template_count_by_path.get(path, 0)))
        # return only the present ones
        return filter(lambda t: 'remote_folder_id' in t, templates_details)

    def report_missing_configurations(self, local_configurations, remote_configurations):
        ids_missing = [local_id for (local_id, remote_id) in remote_configurations.items() if not remote_id]
        for local_id in ids_missing:
            config = local_configurations[local_id]
            self.warnings.append('Missing remote configuration by type [%s] and title [%s]' %
                                 (config['type'], config['title']))


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

    def _normalize(self, id):
        return id[1:] if id.startswith('/') else id

    def _get_referenced_configurations(self, template):
        referenced_configurations = []
        template_json = self._serialize(template)
        for config_id in re.findall('"Configuration/Custom/[\w/]+"', template_json):
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
        query = '?byPath=%s' % urllib.quote(path)
        response = self._request().get('/api/v1/folders/find' + query, contentType='application/json')
        if response.getStatus() == 200:
            return json.loads(response.response)['id']
        elif response.getStatus() == 404:
            return None
        else:
            response.errorDump()
            raise Exception('Request to find a folder [%s] failed with status %d' % (path, response.getStatus()))

    def get_configuration_id_by_type_and_title(self, config_type, config_title, warnings):
        query = '?configurationType=%s&title=%s' % (urllib.quote(config_type), urllib.quote(config_title))
        response = self._request().get('/api/v1/config/byTypeAndTitle' + query, contentType='application/json')
        if response.getStatus() == 200:
            configurations = json.loads(response.response)
            if not configurations:
                return None
            if len(configurations) > 1:
                warnings.append('Found %d configurations by type [%s] and title [%s], choosing the first from: %s' %
                                (len(configurations), config_type, config_title, [c['id'] for c in configurations]))
            return configurations[0]['id']
        else:
            response.errorDump()
            raise Exception('Request to find a configuration [%s/%s] failed with status %d' %
                            (config_type, config_title, response.getStatus()))

    def _request(self):
        return HttpRequest(self.server, self.username, self.password)
