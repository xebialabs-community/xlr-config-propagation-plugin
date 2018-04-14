from com.xebialabs.xlrelease.plugin.webhook import XmlPathResult
from com.xebialabs.deployit.plumbing import CurrentVersion
from com.xebialabs.deployit import ServerConfiguration
from xlrelease.HttpRequest import HttpRequest
from com.xebialabs.deployit.exception import NotFoundException
import re


def push_configuration(connection_details, push_spec, dry_run, xlr_services):
    remote_xlr = RemoteXlr(*connection_details)
    local_xlr = LocalXlr(push_spec, xlr_services)
    source_xlr = local_xlr.get_local_xlr_details()
    target_xlr = remote_xlr.get_xlr_details()
    print('Going to push configuration from XL Release %s (%s) to XL Release %s (%s)' % (
        source_xlr['version'], source_xlr['url'], target_xlr['version'], target_xlr['url']
    ))

    templates_details = local_xlr.get_templates_to_push()

    executed_actions = {
        'templates_details': templates_details
    }
    return executed_actions


# noinspection PyMethodMayBeStatic
class LocalXlr:
    def __init__(self, push_spec, xlr_services):
        self.push_spec = push_spec
        self.template_api = xlr_services['templateApi']
        self.folder_api = xlr_services['folderApi']
        self.configuration_api = xlr_services['configurationApi']
        self.folder_names_cache = {}

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
        parent_id = ci_id.rsplit('/', 1)[0]
        if not parent_id or '/' not in parent_id:
            return ci_path  # stop on 'Applications'
        if parent_id in self.folder_names_cache:
            parent_name = self.folder_names_cache[parent_id]
        else:
            parent_name = self.folder_api.getFolder(parent_id).getTitle()
            self.folder_names_cache[parent_id] = parent_name
        return self._get_name_path(parent_id, parent_name + '/' + ci_path)

    def _matches_spec(self, path, spec):
        return any(map(
            lambda pattern: re.match(pattern, path),
            spec['include']
        ))

    def _normalize(self, id):
        return id[1:] if id.startswith('/') else id

    def _get_referenced_configurations(self, template):
        return []  # TODO: implement

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


class RemoteXlr:
    def __init__(self, server, username, password):
        self.server = server
        self.username = username
        self.password = password

    def get_xlr_details(self):
        request = HttpRequest(self.server, self.username, self.password)
        response = request.get('/server/info', contentType='application/xml')
        if response.isSuccessful():
            version = XmlPathResult(response.response, '/server-info/version').get()
            return {
                'url': self.server['url'],
                'version': version
            }
        else:
            response.errorDump()
            raise Exception('Version request to /server/info failed with status %d' % response.getStatus())
