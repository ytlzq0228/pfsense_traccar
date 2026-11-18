<?php
/* CLI-only rendering test: isolated temporary session; no server auth changes. */
if (PHP_SAPI !== 'cli') { http_response_code(403); exit; }
require_once('config.inc');
$dir = sys_get_temp_dir() . '/traccar-render-' . bin2hex(random_bytes(8));
mkdir($dir, 0700);
session_save_path($dir);
session_start();
$_SESSION = ['Logged_In'=>'True','Username'=>'admin','Post_Login'=>true,'user_radius_attributes'=>[],
    'last_access'=>time(),'protocol'=>config_get_path('system/webgui/protocol'),'REMOTE_ADDR'=>'127.0.0.1'];
session_write_close();
$_SERVER['REQUEST_METHOD']='GET';
$_SERVER['REQUEST_URI']='/services_traccar.php';
$_SERVER['SCRIPT_NAME']='/services_traccar.php';
$_SERVER['SCRIPT_FILENAME']='/usr/local/www/services_traccar.php';
$_SERVER['DOCUMENT_ROOT']='/usr/local/www';
$_SERVER['REMOTE_ADDR']='127.0.0.1';
$_SERVER['SERVER_ADDR']='10.0.9.1';
$_SERVER['SERVER_NAME']='10.0.9.1';
$_SERVER['SERVER_PORT']='443';
$_SERVER['HTTP_HOST']='10.0.9.1';
$_SERVER['HTTPS']='on';
$mode = $argv[1] ?? 'settings';
$_GET=['tab'=>$mode];
$_POST=[];
if ($mode === 'dashboard') {
    $_GET=[];
    $_SERVER['REQUEST_URI']='/index.php';
    $_SERVER['SCRIPT_NAME']='/index.php';
    $_SERVER['SCRIPT_FILENAME']='/usr/local/www/index.php';
}
if ($mode === 'status-json') { $_GET=['action'=>'status']; }
if ($mode === 'invalid-save' || $mode === 'save-current') {
    require_once('/usr/local/pkg/traccar.inc');
    $view = $argv[2] ?? ($mode === 'invalid-save' ? 'traccar' : 'system');
    $_GET = ['tab' => $view];
    $current = traccar_config();
    $editable = [];
    foreach (traccar_fields($view) as $section) { $editable = array_merge($editable, $section); }
    foreach ($editable as $key=>$spec) {
        $value = $current[$key];
        if (in_array($key,['aprs_passcode','ntrip_password'],true)) { $_POST[$key]=''; continue; }
        if (is_bool($value)) { if ($value) { $_POST[$key]='yes'; } }
        else { $_POST[$key]=(string)$value; }
    }
    $_POST['save']='save';
    if ($mode === 'invalid-save') { $_POST['upload_rate']='0'; }
    $_SERVER['REQUEST_METHOD']='POST';
    // Only this isolated CLI harness skips browser CSRF middleware.
    $nocsrf = true;
}
register_shutdown_function(function() use ($dir) {
    foreach (glob($dir.'/*') as $file) { unlink($file); }
    rmdir($dir);
});
chdir('/usr/local/www');
require($mode === 'dashboard' ? '/usr/local/www/index.php' : '/usr/local/www/services_traccar.php');
