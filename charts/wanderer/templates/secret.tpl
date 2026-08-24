{{- if not .Values.secret.existingSecret }}
apiVersion: v1
kind: Secret
metadata:
  name: {{ include "wanderer.secretName" . }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "wanderer.labels" . | nindent 4 }}
  {{- with .Values.secret.annotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
type: Opaque
data:
  {{- include "wanderer.secretData" . | nindent 2 }}
{{- end }}
