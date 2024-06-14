import logging
import uuid

from django.db.models.signals import m2m_changed, post_save, pre_delete
from django.test import RequestFactory
from django.urls import reverse

from core.choices import JobStatusChoices
from extras.context_managers import event_tracking
from extras.signals import handle_changed_object, handle_deleted_object
from rq.timeouts import JobTimeoutException
from utilities.exceptions import AbortTransaction

from .choices import BranchStatusChoices
from .models import Branch

__all__ = (
    'merge_branch',
    'provision_branch',
    'sync_branch',
)


logger = logging.getLogger(__name__)


def provision_branch(job):
    branch = Branch.objects.get(pk=job.object_id)

    try:
        job.start()
        logger.info(f"Provisioning branch {branch} ({branch.schema_name})")
        branch.provision()
        job.terminate()

    except Exception as e:
        job.terminate(status=JobStatusChoices.STATUS_ERRORED, error=repr(e))
        Branch.objects.filter(pk=branch.pk).update(status=BranchStatusChoices.FAILED)
        if type(e) is JobTimeoutException:
            logging.error(e)
        else:
            raise e


def merge_branch(job, commit=True, request_id=None):
    branch = Branch.objects.get(pk=job.object_id)

    # Create a dummy request for the event_tracking() branch manager
    request = RequestFactory().get(reverse('home'))
    request.id = request_id or uuid.uuid4()
    request.user = job.user

    try:
        job.start()
        logger.info(f"Merging branch {branch} ({branch.schema_name})")
        with event_tracking(request):
            branch.merge(commit=commit)
        job.terminate()

    except AbortTransaction:
        job.terminate(status=JobStatusChoices.STATUS_COMPLETED)
        Branch.objects.filter(pk=branch.pk).update(status=BranchStatusChoices.READY)

    except Exception as e:
        job.terminate(status=JobStatusChoices.STATUS_ERRORED, error=repr(e))
        Branch.objects.filter(pk=branch.pk).update(status=BranchStatusChoices.FAILED)
        if type(e) is JobTimeoutException:
            logging.error(e)
        else:
            raise e


def sync_branch(job, commit=True):
    branch = Branch.objects.get(pk=job.object_id)

    try:
        job.start()
        logger.info(f"Rebasing branch {branch} ({branch.schema_name})")

        # Disconnect changelog handlers
        post_save.disconnect(handle_changed_object)
        m2m_changed.disconnect(handle_changed_object)
        pre_delete.disconnect(handle_deleted_object)

        branch.sync(commit=commit)

        job.terminate()

    except AbortTransaction:
        job.terminate(status=JobStatusChoices.STATUS_COMPLETED)
        Branch.objects.filter(pk=branch.pk).update(status=BranchStatusChoices.READY)

    except Exception as e:
        job.terminate(status=JobStatusChoices.STATUS_ERRORED, error=repr(e))
        Branch.objects.filter(pk=branch.pk).update(status=BranchStatusChoices.FAILED)
        logging.error(e)

    finally:
        # Reconnect signal handlers
        post_save.connect(handle_changed_object)
        m2m_changed.connect(handle_changed_object)
        pre_delete.connect(handle_deleted_object)
