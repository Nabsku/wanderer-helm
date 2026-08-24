apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "wanderer.webServiceName" . }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "wanderer.labels" . | nindent 4 }}
    app.kubernetes.io/component: web
spec:
  replicas: {{ .Values.web.replicaCount }}
  revisionHistoryLimit: {{ .Values.web.revisionHistoryLimit }}
  strategy:
    type: {{ .Values.web.strategy.type }}
  selector:
    matchLabels:
      {{- include "wanderer.selectorLabels" . | nindent 6 }}
      app.kubernetes.io/component: web
  template:
    metadata:
      annotations:
        checksum/wanderer-secret: {{ include "wanderer.secretChecksum" . | quote }}
        {{- with .Values.web.podAnnotations }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
      labels:
        {{- include "wanderer.labels" . | nindent 8 }}
        app.kubernetes.io/component: web
        {{- with .Values.web.podLabels }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
    spec:
      serviceAccountName: {{ include "wanderer.serviceAccountName" . }}
      automountServiceAccountToken: {{ .Values.serviceAccount.automountServiceAccountToken }}
      terminationGracePeriodSeconds: {{ .Values.web.terminationGracePeriodSeconds }}
      {{- with .Values.imagePullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.web.podSecurityContext }}
      securityContext:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.web.nodeSelector }}
      nodeSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.web.affinity }}
      affinity:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.web.tolerations }}
      tolerations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.web.topologySpreadConstraints }}
      topologySpreadConstraints:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      initContainers:
        - name: wait-for-dependencies
          image: curlimages/curl:8.18.0
          imagePullPolicy: IfNotPresent
          command: ["/bin/sh", "-ec"]
          args:
            - >-
              until curl --fail --silent --show-error --max-time 5
              http://{{ include "wanderer.databaseServiceName" . }}:{{ .Values.database.service.port }}/health
              && curl --fail --silent --show-error --max-time 5
              http://{{ include "wanderer.searchServiceName" . }}:{{ .Values.search.service.port }}/health;
              do sleep 2; done
      containers:
        - name: web
          image: {{ include "wanderer.image" .Values.web.image | quote }}
          imagePullPolicy: {{ .Values.web.image.pullPolicy }}
          securityContext:
            {{- toYaml .Values.web.securityContext | nindent 12 }}
          ports:
            - name: http
              containerPort: 3000
              protocol: TCP
          env:
            - name: ORIGIN
              value: {{ include "wanderer.origin" . | quote }}
            - name: MEILI_URL
              value: {{ printf "http://%s:%d" (include "wanderer.searchServiceName" .) (.Values.search.service.port | int) | quote }}
            - name: BODY_SIZE_LIMIT
              value: {{ .Values.web.config.bodySizeLimit | quote }}
            - name: PUBLIC_POCKETBASE_URL
              value: {{ include "wanderer.publicPocketbaseURL" . | quote }}
            - name: PUBLIC_DISABLE_SIGNUP
              value: {{ .Values.web.config.disableSignup | quote }}
            - name: PUBLIC_PRIVATE_INSTANCE
              value: {{ .Values.web.config.privateInstance | quote }}
            - name: PUBLIC_MAP_MAX_POLYLINES
              value: {{ .Values.web.config.mapMaxPolylines | quote }}
            - name: VALHALLA_URL
              value: {{ .Values.web.config.valhallaURL | quote }}
            - name: NOMINATIM_URL
              value: {{ .Values.web.config.nominatimURL | quote }}
            - name: OVERPASS_API_URL
              value: {{ .Values.web.config.overpassURL | quote }}
            - name: UPLOAD_FOLDER
              value: /app/uploads
            {{- with .Values.web.extraEnv }}
            {{- toYaml . | nindent 12 }}
            {{- end }}
          {{- with .Values.web.extraEnvFrom }}
          envFrom:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          volumeMounts:
            - name: uploads
              mountPath: /app/uploads
            - name: tmp
              mountPath: /tmp
          {{- with .Values.web.startupProbe }}
          {{- if .enabled }}
          startupProbe:
            httpGet:
              path: {{ .path }}
              port: http
            periodSeconds: {{ .periodSeconds }}
            timeoutSeconds: {{ .timeoutSeconds }}
            failureThreshold: {{ .failureThreshold }}
          {{- end }}
          {{- end }}
          {{- with .Values.web.livenessProbe }}
          {{- if .enabled }}
          livenessProbe:
            {{- if eq .type "tcp" }}
            tcpSocket:
              port: http
            {{- else }}
            httpGet:
              path: {{ .path }}
              port: http
            {{- end }}
            periodSeconds: {{ .periodSeconds }}
            timeoutSeconds: {{ .timeoutSeconds }}
            failureThreshold: {{ .failureThreshold }}
          {{- end }}
          {{- end }}
          {{- with .Values.web.readinessProbe }}
          {{- if .enabled }}
          readinessProbe:
            httpGet:
              path: {{ .path }}
              port: http
            periodSeconds: {{ .periodSeconds }}
            timeoutSeconds: {{ .timeoutSeconds }}
            failureThreshold: {{ .failureThreshold }}
          {{- end }}
          {{- end }}
          resources:
            {{- toYaml .Values.web.resources | nindent 12 }}
      volumes:
        - name: uploads
          {{- if .Values.web.persistence.enabled }}
          persistentVolumeClaim:
            claimName: {{ default (printf "%s-web-uploads" (include "wanderer.fullname" .)) .Values.web.persistence.existingClaim }}
          {{- else }}
          emptyDir: {}
          {{- end }}
        - name: tmp
          emptyDir: {}
