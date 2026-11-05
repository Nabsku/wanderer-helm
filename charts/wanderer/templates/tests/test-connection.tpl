apiVersion: v1
kind: Pod
metadata:
  name: "{{ include "wanderer.fullname" . }}-connectivity"
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "wanderer.labels" . | nindent 4 }}
    app.kubernetes.io/component: test
  annotations:
    "helm.sh/hook": test
    "helm.sh/hook-delete-policy": before-hook-creation,hook-succeeded
spec:
  restartPolicy: Never
  automountServiceAccountToken: false
  containers:
    - name: curl
      image: {{ include "wanderer.image" .Values.tools.curl.image | quote }}
      imagePullPolicy: {{ .Values.tools.curl.image.pullPolicy }}
      command:
        - sh
        - -ec
        - >-
          curl --fail --silent --show-error
          http://{{ include "wanderer.webServiceName" . }}:{{ .Values.web.service.port }}/
