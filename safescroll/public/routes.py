from flask import flash, redirect, render_template, url_for

from safescroll.extensions import db
from safescroll.forms import ContactForm
from safescroll.models import ContactMessage
from safescroll.public import bp
from safescroll.utils import normalize_email


@bp.route("/")
def home():
    return render_template("public/index.html")


@bp.route("/privacy")
def privacy():
    return render_template("public/privacy.html")


@bp.route("/terms")
def terms():
    return render_template("public/terms.html")


@bp.route("/contact", methods=["GET", "POST"])
def contact():
    form = ContactForm()
    if form.validate_on_submit():
        try:
            email = normalize_email(form.email.data)
        except ValueError as exc:
            form.email.errors.append(str(exc))
        else:
            db.session.add(
                ContactMessage(
                    name=form.name.data.strip(),
                    email=email,
                    message=form.message.data.strip(),
                )
            )
            db.session.commit()
            flash("Thanks—your message has been received.", "success")
            return redirect(url_for("public.contact"))
    return render_template("public/contact.html", form=form)
