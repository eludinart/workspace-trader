<?php
/**
 * Upsert VAPID + WEB_PUSH_ENABLED on Coolify trader (app id 6).
 * Reads secrets from /tmp/vapid-*.txt — never hardcode private keys here.
 */
require "/var/www/html/vendor/autoload.php";
$app = require_once "/var/www/html/bootstrap/app.php";
$app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap();

$appId = 6;
$public = trim((string) @file_get_contents("/tmp/vapid-public.txt"));
$private = trim((string) @file_get_contents("/tmp/vapid-private.txt"));
$subject = trim((string) @file_get_contents("/tmp/vapid-subject.txt"));
if ($subject === "") {
    $subject = "mailto:ops@eludein.art";
}

if ($public === "" || $private === "") {
    fwrite(STDERR, "missing /tmp/vapid-public.txt or /tmp/vapid-private.txt\n");
    exit(1);
}

function upsertEnv(int $appId, string $key, string $value): void
{
    $existing = App\Models\EnvironmentVariable::query()
        ->where("resourceable_type", App\Models\Application::class)
        ->where("resourceable_id", $appId)
        ->where("key", $key)
        ->where("is_preview", false)
        ->first();

    if ($existing) {
        $existing->value = $value;
        $existing->is_literal = true;
        $existing->is_runtime = true;
        $existing->is_buildtime = true;
        $existing->save();
        echo "updated {$key}\n";
        return;
    }

    App\Models\EnvironmentVariable::create([
        "key" => $key,
        "value" => $value,
        "is_literal" => true,
        "is_runtime" => true,
        "is_buildtime" => true,
        "is_preview" => false,
        "resourceable_type" => App\Models\Application::class,
        "resourceable_id" => $appId,
    ]);
    echo "created {$key}\n";
}

upsertEnv($appId, "VAPID_PUBLIC_KEY", $public);
upsertEnv($appId, "NEXT_PUBLIC_VAPID_PUBLIC_KEY", $public);
upsertEnv($appId, "VAPID_PRIVATE_KEY", $private);
upsertEnv($appId, "VAPID_SUBJECT", $subject);
upsertEnv($appId, "WEB_PUSH_ENABLED", "true");
echo "VAPID_OK\n";
