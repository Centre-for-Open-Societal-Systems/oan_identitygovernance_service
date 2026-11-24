{{- define "rbac.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{ default (include "common.names.fullname" .) .Values.serviceAccount.name }}
{{- else -}}
{{ default "default" .Values.serviceAccount.name }}
{{- end -}}
{{- end -}}

{{- define "rbac.imagePullSecrets" -}}
{{- include "common.images.pullSecrets" (dict "images" (list .Values.image) "global" .Values.global) -}}
{{- end -}}

{{/*
Render env values. Strings go through tpl; other maps are valueFrom blocks.
*/}}
{{- define "rbac.envVars" -}}
{{- $context := . -}}
{{- range $k, $v := .Values.envVars }}
- name: {{ $k }}
{{- if or (kindIs "int64" $v) (kindIs "float64" $v) (kindIs "bool" $v) }}
  value: {{ $v | quote }}
{{- else }}
  value: {{ tpl $v $context | squote }}
{{- end }}
{{- end }}
{{- range $k, $v := .Values.envVarsFrom }}
- name: {{ $k }}
  valueFrom: {{- include "common.tplvalues.render" (dict "value" $v "context" $context) | nindent 4 }}
{{- end }}
{{- end -}}
