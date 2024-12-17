from flask import Flask, request, render_template, redirect, url_for
import pyodbc
import logging
from azure.storage.blob import BlobServiceClient
import pyodbc
from azure.communication.email import EmailClient
from openai import AzureOpenAI

app = Flask(__name__)

# Configuration de la connexion à la base de données SQL Server
connection_string = (
    'Driver={ODBC Driver 18 for SQL Server};'
    'Server=tcp:companyiliasetammar.database.windows.net,1433;'
    'Database=company;'
    'Uid=iliasetammar;'
    'Pwd=;'
    'Encrypt=yes;'
    'TrustServerCertificate=no;'
    'Connection Timeout=30;'
)

# Configuration Azure Blob Storage
AZURE_STORAGE_CONNECTION_STRING = ""
BLOB_CONTAINER_NAME = "images" 
blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)

# Configuration Azure OpenAI
Client = AzureOpenAI(
    api_key="",
    azure_endpoint="",
    api_version="2024-08-01-preview",
    azure_deployment="gpt-35-turbo-16k"
)

# Configuration Azure Communication Services
ACS_CONNECTION_STRING = "" 
EMAIL_SENDER = "da952067-aa90-4451-ab15-050b49a73bf4.azurecomm.net"
SENDER_NAME = "L'Administrateur" 



# Fonction pour télécharger un fichier dans Azure Blob Storage
def upload_to_blob(file):
    blob_client = blob_service_client.get_blob_client(container=BLOB_CONTAINER_NAME, blob=file.filename)
    blob_client.upload_blob(file, overwrite=True)
    return f"https://{blob_service_client.account_name}.blob.core.windows.net/{BLOB_CONTAINER_NAME}/{file.filename}"

# Fonction pour vérifier les extensions de fichier
def allowed_file(filename):
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'} 
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Fonction pour exécuter une requête SQL
def execute_query(query, params=()):
    """
    Helper function to execute a database query.
    """
    try:
        conn = pyodbc.connect(connection_string)
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
        cursor.close()
        conn.close()
        return None
    except pyodbc.Error as e:
        return str(e)

# Routes
@app.route('/')
def admin_panel():

    message = request.args.get('message')
    message_type = request.args.get('message_type')

    users, teams, tasks = [], [], []

    try:
        conn = pyodbc.connect(connection_string)
        cursor = conn.cursor()

        cursor.execute("SELECT user_id, user_name, email, image_link, is_admin FROM users")
        users = cursor.fetchall()

        cursor.execute("SELECT team_id, current_task FROM teams")
        teams = cursor.fetchall()

        cursor.execute("""
            SELECT t.team_id, t.current_task, utm.user_id, u.user_name
            FROM teams t
            LEFT JOIN user_team_mapping utm ON t.team_id = utm.team_id
            LEFT JOIN users u ON utm.user_id = u.user_id
        """)
        tasks = cursor.fetchall()

        cursor.close()
        conn.close()
    except pyodbc.Error as e:
        message = f"Error fetching data: {e}"
        message_type = "danger"

    return render_template(
        'admin_management_panel.html',
        users=users,
        teams=teams,
        tasks=tasks,
        message=message,
        message_type=message_type
    )


@app.route('/create_user', methods=['POST'])
def create_user():
    user_name = request.form['user_name']
    email = request.form['email']
    password = request.form['password']
    is_admin = 1 if request.form.get('is_admin') == 'true' else 0

    photo = request.files.get('photo')

    photo_url = None

    if photo and photo.filename != '':
        if not allowed_file(photo.filename):
            return redirect(url_for('admin_panel', message="Type de fichier non supporté. Seuls PNG, JPG, JPEG, GIF, et WEBP sont autorisés.", message_type='danger'))
        try:
            photo_url = upload_to_blob(photo)
        except Exception as e:
            return redirect(url_for('admin_panel', message=f"Erreur de téléchargement de l'image : {e}", message_type='danger'))

    query = """
        INSERT INTO users (user_name, email, password, image_link, is_admin)
        VALUES (?, ?, ?, ?, ?)
    """
    params = (user_name, email, password, photo_url, is_admin)
    error = execute_query(query, params)
    if error:
        return redirect(url_for('admin_panel', message=f"Error: {error}", message_type='danger'))
    
    return redirect(url_for('admin_panel', message="Utilisateur créé avec succès !", message_type='success'))


@app.route('/create_team', methods=['POST'])
def create_team():
    team_task = request.form['team_task']
    query = "INSERT INTO teams (current_task) VALUES (?)"
    error = execute_query(query, (team_task,))
    if error:
        return redirect(url_for('admin_panel', message=f"Error: {error}", message_type='danger'))
    return redirect(url_for('admin_panel', message="Team created successfully!", message_type='success'))


@app.route('/assign_user_to_team', methods=['POST'])
def assign_user_to_team():
    team_id = request.form['team_id']
    user_id = request.form['user_id']

    # Vérifier si l'utilisateur est déjà assigné à l'équipe
    query_check = "SELECT COUNT(*) FROM user_team_mapping WHERE user_id = ? AND team_id = ?"
    try:
        conn = pyodbc.connect(connection_string)
        cursor = conn.cursor()
        cursor.execute(query_check, (user_id, team_id))
        result = cursor.fetchone()
        cursor.close()
        conn.close()
    except pyodbc.Error as e:
        return redirect(url_for('admin_panel', message=f"Erreur de vérification : {e}", message_type='danger'))

    if result[0] > 0:
        return redirect(url_for('admin_panel', message="Utilisateur déjà assigné.", message_type='warning'))

    # Insérer l'utilisateur dans l'équipe
    query_insert = "INSERT INTO user_team_mapping (user_id, team_id) VALUES (?, ?)"
    error = execute_query(query_insert, (user_id, team_id))
    if error:
        return redirect(url_for('admin_panel', message=f"Erreur d'assignation : {error}", message_type='danger'))

    # Récupérer les détails de la tâche et envoyer l'email
    try:
        conn = pyodbc.connect(connection_string)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT t.current_task, u.email 
            FROM teams t
            INNER JOIN user_team_mapping utm ON t.team_id = utm.team_id
            INNER JOIN users u ON utm.user_id = u.user_id
            WHERE t.team_id = ? AND u.user_id = ?
        """, (team_id, user_id))
        result = cursor.fetchone()
        cursor.close()
        conn.close()

        if result:
            task_description, user_email = result

            # Generating email content using Azure OpenAI GPT-4
            response = Client.chat.completions.create(
                model="gpt-35-turbo-16k",
                messages=[
                    {
                        "role": "system",
                        "content": """Vous êtes un système automatisé d'envoi d'emails.
                        Générez des messages courts et formels.
                        Incluez toujours une mention 'Ne pas répondre'."""
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Rédigez un email automatique pour informer {user_email} "
                            f"de son assignation à la tâche : '{task_description}'. "
                            "Consignes:"
                            "- Maximum 3 phrases"
                            "- Ton formel et impersonnel"
                            "- Indiquer qu'il s'agit d'un message automatique"
                            "- Préciser de ne pas répondre à cet email"
                            f"Utilisez '{SENDER_NAME}' comme nom de l'expéditeur."
                        )
                    }
                ]
            )

            # Extracting the message content properly
            email_content = response.choices[0].message.content

            # Sending the email using Azure Communication Services
            send_email_via_acs(user_email, "Nouvelle tâche assignée", email_content)


    except Exception as e:
        logging.error(f"Erreur lors de l'envoi de l'email : {e}")

    return redirect(url_for('admin_panel', message="Utilisateur assigné avec succès !", message_type='success'))


@app.route('/remove_user_from_team', methods=['POST'])
def remove_user_from_team():
    team_id = request.form.get('team_id')
    user_id = request.form.get('user_id')

    print(f"Reçu : team_id={team_id}, user_id={user_id}")  # Debugging

    if not team_id or not user_id:
        return redirect(url_for('admin_panel', message="Erreur : Identifiant d'équipe ou d'utilisateur manquant.", message_type='danger'))

    query = "DELETE FROM user_team_mapping WHERE user_id = ? AND team_id = ?"
    error = execute_query(query, (user_id, team_id))

    if error:
        return redirect(url_for('admin_panel', message=f"Erreur lors de la suppression : {error}", message_type='danger'))

    return redirect(url_for('admin_panel', message="Utilisateur supprimé de l'équipe avec succès !", message_type='success'))


@app.route('/remove_user', methods=['POST'])
def remove_user():
    user_id = request.form['user_id']
    team_id = request.form['team_id']
    query = "DELETE FROM user_team_mapping WHERE user_id = ? AND team_id = ?"
    error = execute_query(query, (user_id, team_id))
    if error:
        return redirect(url_for('admin_panel', message=f"Error: {error}", message_type='danger'))
    return redirect(url_for('admin_panel', message="User removed from team successfully!", message_type='success'))


@app.route('/delete_user', methods=['POST'])
def delete_user():
    user_id = request.form['user_id']
    query = "DELETE FROM users WHERE user_id = ?"
    error = execute_query(query, (user_id,))
    if error:
        return redirect(url_for('admin_panel', message=f"Error: {error}", message_type='danger'))
    return redirect(url_for('admin_panel', message="User deleted successfully!", message_type='success'))


@app.route('/delete_team', methods=['POST'])
def delete_team():
    team_id = request.form['team_id']
    query = "DELETE FROM teams WHERE team_id = ?"
    error = execute_query(query, (team_id,))
    if error:
        return redirect(url_for('admin_panel', message=f"Error: {error}", message_type='danger'))
    return redirect(url_for('admin_panel', message="Team deleted successfully!", message_type='success'))


@app.route('/edit_user', methods=['POST'])
def edit_user():
    user_id = request.form['user_id']
    user_name = request.form['user_name']
    email = request.form['email']
    is_admin = 1 if request.form['is_admin'] == 'true' else 0

    photo = request.files.get('photo')
    photo_url = None

    if photo and photo.filename != '':
        if not allowed_file(photo.filename):
            return redirect(url_for('admin_panel', message="Type de fichier non supporté.", message_type='danger'))
        try:
            photo_url = upload_to_blob(photo)
        except Exception as e:
            return redirect(url_for('admin_panel', message=f"Erreur de téléchargement de l'image : {e}", message_type='danger'))

    if photo_url:
        query = """
            UPDATE users
            SET user_name = ?, email = ?, is_admin = ?, image_link = ?
            WHERE user_id = ?
        """
        params = (user_name, email, is_admin, photo_url, user_id)
    else:
        query = """
            UPDATE users
            SET user_name = ?, email = ?, is_admin = ?
            WHERE user_id = ?
        """
        params = (user_name, email, is_admin, user_id)

    error = execute_query(query, params)
    if error:
        return redirect(url_for('admin_panel', message=f"Error: {error}", message_type='danger'))
    
    return redirect(url_for('admin_panel', message="Utilisateur mis à jour avec succès !", message_type='success'))


@app.route('/edit_team', methods=['POST'])
def edit_team():
    team_id = request.form['team_id']
    current_task = request.form['current_task']

    query = """
        UPDATE teams
        SET current_task = ?
        WHERE team_id = ?
    """
    error = execute_query(query, (current_task, team_id))
    if error:
        return redirect(url_for('admin_panel', message=f"Error: {error}", message_type='danger'))
    return redirect(url_for('admin_panel', message="Team updated successfully!", message_type='success'))

def send_email_via_acs(email_to, subject, content):
    try:
        email_client = EmailClient.from_connection_string(ACS_CONNECTION_STRING)
        
        formatted_content = content.replace("\n", "<br>")
        
        email_message = {
            "content": {
                "subject": subject,
                "plainText": content,
                "html": f"<html><body>{formatted_content}</body></html>"
            },
            "recipients": {
                "to": [{"address": email_to}]
            },
            "senderAddress": f"DoNotReply@{EMAIL_SENDER}"
        }

        # La méthode begin_send retourne un poller, nous devons attendre le résultat
        poller = email_client.begin_send(email_message)
        result = poller.result()
        
        # Log de la réponse complète pour le débogage
        logging.info(f"Email envoyé avec succès. Réponse : {result}")
        
    except Exception as e:
        logging.error(f"Erreur lors de l'envoi de l'email via ACS : {e}")
        logging.error(f"Détails de l'erreur : {str(e)}")
        raise


if __name__ == "__main__":
    app.run(debug=True)
