{{- if and .Values.database.persistence.data.enabled (not .Values.database.persistence.data.existingClaim) }}
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ include "wanderer.fullname" . }}-database-data
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "wanderer.labels" . | nindent 4 }}
    app.kubernetes.io/component: database
  annotations:
    {{- include "wanderer.pvcAnnotations" .Values.database.persistence.data | nindent 4 }}
spec:
  accessModes:
    {{- toYaml .Values.database.persistence.data.accessModes | nindent 4 }}
  resources:
    requests:
      storage: {{ .Values.database.persistence.data.size }}
  {{- with .Values.database.persistence.data.storageClass }}
  storageClassName: {{ . | quote }}
  {{- end }}
{{- end }}
---
{{- if and .Values.database.persistence.plugins.enabled (not .Values.database.persistence.plugins.existingClaim) }}
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ include "wanderer.fullname" . }}-database-plugins
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "wanderer.labels" . | nindent 4 }}
    app.kubernetes.io/component: database
  annotations:
    {{- include "wanderer.pvcAnnotations" .Values.database.persistence.plugins | nindent 4 }}
spec:
  accessModes:
    {{- toYaml .Values.database.persistence.plugins.accessModes | nindent 4 }}
  resources:
    requests:
      storage: {{ .Values.database.persistence.plugins.size }}
  {{- with .Values.database.persistence.plugins.storageClass }}
  storageClassName: {{ . | quote }}
  {{- end }}
{{- end }}
