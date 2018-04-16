from com.xebialabs.deployit.plumbing import CurrentVersion
from com.xebialabs.deployit import ServerConfiguration
from com.xebialabs.deployit.exception import NotFoundException
from xlrconfig import get_parent
import re


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
        path = self.get_name_path(ci_id, template.getTitle())
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

    def get_name_path(self, ci_id, ci_path):
        parent_id = get_parent(ci_id)
        if not parent_id or '/' not in parent_id:
            return ci_path  # stop on 'Applications'
        if parent_id in self._folder_names_cache:
            parent_name = self._folder_names_cache[parent_id]
        else:
            parent_name = self.folder_api.getFolder(parent_id).getTitle()
            self._folder_names_cache[parent_id] = parent_name
        return self.get_name_path(parent_id, parent_name + '/' + ci_path)

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
        template_json = self.to_json(template)
        for config_id in set(re.findall('"Configuration/Custom/[\w /]+"', template_json)):
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
                    referenced_template_path = self.get_name_path(referenced_template_id,
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

    def get_template(self, template_id):
        return self.template_api.getTemplate(template_id)

    def strip_attachments_and_warn(self, template, warnings):
        if template.getAttachments():
            warnings.append('Skipping export of %d attachments of template [%s](%s) as it is not supported yet' % (
                len(template.getAttachments()), template.getTitle(), template.getId()
            ))
            template.setAttachments([])
            for task in template.getAllTasks():
                task.setAttachments([])

    def check_triggers_and_warn(self, template, warnings):
        if template.getReleaseTriggers():
            warnings.append('Template [%s](%s) has %d triggers, enable them manually after the import' % (
                template.getTitle(), template.getId(), len(template.getReleaseTriggers())
            ))

    # noinspection PyProtectedMember
    def to_json(self, ci):
        from com.xebialabs.xlrelease.json import CiSerializerHelper
        if ci._delegate:
            return CiSerializerHelper.serialize(ci._delegate)
        else:
            return CiSerializerHelper.serialize(ci)
